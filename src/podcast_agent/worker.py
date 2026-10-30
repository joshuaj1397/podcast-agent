"""Cloud worker: SQS long-poll loop around Pipeline.run_file (the ECS Fargate entrypoint in infra/terraform).

Message -> download transcript from S3 -> idempotency check -> run the agent -> upload report.{json,md} and
trace.jsonl -> send ⚠ claims to the human-review queue -> mark the job done -> delete the message.
On any error the message is NOT deleted: SQS makes it visible again after the visibility timeout, and after
maxReceiveCount attempts the redrive policy moves it to the dead-letter queue.

Environment (set by the Terraform task definition): PODCAST_JOBS_QUEUE_URL, PODCAST_RESULTS_BUCKET,
optional PODCAST_JOBS_TABLE (DynamoDB job status), PODCAST_REVIEW_QUEUE_URL, PODCAST_HARNESS (path),
PODCAST_MODEL / PODCAST_FALLBACK_MODELS (see llm.make_model).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote_plus

from . import harness
from .config import Settings
from .models import Report
from .trace import console


def parse_s3_event(body: dict) -> tuple[str, str]:
    """(bucket, key) from an EventBridge "Object Created" event or a classic S3 notification."""
    if "detail" in body:  # EventBridge
        return body["detail"]["bucket"]["name"], unquote_plus(body["detail"]["object"]["key"])
    record = body["Records"][0]["s3"]  # S3 -> SQS notification
    return record["bucket"]["name"], unquote_plus(record["object"]["key"])


def client_of(key: str) -> str:
    """Uploads land under incoming/<client>/<show>/<episode>; anything else belongs to "default"."""
    parts = key.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "incoming" else "default"


def job_id(content: bytes, harness_version: str, model: str) -> str:
    """Same transcript + same harness + same model = same job: re-deliveries become no-ops."""
    return hashlib.sha256(content + f"|{harness_version}|{model}".encode()).hexdigest()[:20]


class Worker:
    def __init__(self) -> None:
        import boto3  # optional dependency: `uv sync --extra aws`

        self.queue_url = os.environ["PODCAST_JOBS_QUEUE_URL"]
        self.results_bucket = os.environ["PODCAST_RESULTS_BUCKET"]
        self.review_queue_url = os.environ.get("PODCAST_REVIEW_QUEUE_URL")
        self.sqs, self.s3 = boto3.client("sqs"), boto3.client("s3")
        table = os.environ.get("PODCAST_JOBS_TABLE")
        self.jobs = boto3.resource("dynamodb").Table(table) if table else None
        self.spec = harness.load(os.environ.get("PODCAST_HARNESS", "harness/base.yaml"))
        self.settings = Settings()
        self.model = self.settings.model_override or self.spec.model

    def run(self, once: bool = False) -> None:
        console.print(f"worker polling {self.queue_url} (harness {self.spec.version}, model {self.model})")
        while True:
            resp = self.sqs.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20,
                                            AttributeNames=["ApproximateReceiveCount"])
            for msg in resp.get("Messages", []):
                try:
                    self.handle(json.loads(msg["Body"]))
                    self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=msg["ReceiptHandle"])
                except Exception as err:  # leave the message: SQS retries it, then the DLQ takes it
                    console.print(f"[red]job failed[/] (attempt {msg['Attributes']['ApproximateReceiveCount']}): "
                                  f"{type(err).__name__}: {err}")
            if once:
                return

    def handle(self, body: dict) -> None:
        bucket, key = parse_s3_event(body)
        content = self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        jid = job_id(content, self.spec.version, self.model)
        if self._status(jid) in ("SUCCEEDED", "NEEDS_REVIEW"):
            console.print(f"job {jid} for s3://{bucket}/{key} already done; skipping")
            return
        self._set_status(jid, "RUNNING", source=f"s3://{bucket}/{key}")
        from .pipeline import Pipeline

        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / Path(key).name
            local.write_bytes(content)
            settings = replace(self.settings, out_dir=Path(tmp) / "out")
            t0 = time.time()
            report = asyncio.run(Pipeline(self.spec, settings).run_file(local))
            prefix = f"results/{client_of(key)}/{report.episode_id}/{jid}"
            for f in (settings.out_dir / report.episode_id).iterdir():
                self.s3.upload_file(str(f), self.results_bucket, f"{prefix}/{f.name}")
        flagged = self._send_for_review(report, jid, prefix)
        self._set_status(jid, "NEEDS_REVIEW" if flagged else "SUCCEEDED",
                         results=f"s3://{self.results_bucket}/{prefix}/", client_id=client_of(key),
                         episode_id=report.episode_id, harness_version=self.spec.version, model_version=self.model,
                         seconds=round(time.time() - t0, 1), degraded=len(report.degraded))

    def _send_for_review(self, report: Report, jid: str, prefix: str) -> bool:
        flagged = [fc.model_dump(include={"claim", "speaker", "ts", "verification", "confidence"})
                   for fc in report.fact_checks if fc.status == "inaccurate"]
        if flagged and self.review_queue_url:
            self.sqs.send_message(QueueUrl=self.review_queue_url, MessageBody=json.dumps(
                {"job_id": jid, "episode_id": report.episode_id, "results": prefix, "claims": flagged}))
        return bool(flagged)

    def _status(self, jid: str) -> str | None:
        if self.jobs is None:
            return None
        return self.jobs.get_item(Key={"pk": f"JOB#{jid}", "sk": "META"}).get("Item", {}).get("status")

    def _set_status(self, jid: str, status: str, **fields: object) -> None:
        if self.jobs is not None:
            # Table layout from infra/terraform/modules/data: pk=JOB#<idempotency key>, sk=META.
            self.jobs.put_item(Item={"pk": f"JOB#{jid}", "sk": "META", "status": status,
                                     "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                     **{k: str(v) for k, v in fields.items()}})
