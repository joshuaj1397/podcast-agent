terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

data "aws_caller_identity" "current" {}

# Flow: s3://<upload>/incoming/<client>/<show>/<episode>.(json|txt)
#   -> S3 "Object Created" event on the default EventBridge bus
#   -> rule (prefix + suffix filter) -> SQS jobs queue (SSE-KMS)
#   -> ECS workers (long polling); after max_receive_count failed receives -> DLQ.
# Episodes with ⚠ (inaccurate/outdated) claims are also put on the review queue for a human editor.

# ---------------------------------------------------------------- upload bucket

resource "aws_s3_bucket" "upload" {
  bucket        = "${var.name_prefix}-upload-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_ownership_controls" "upload" {
  bucket = aws_s3_bucket.upload.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "upload" {
  bucket                  = aws_s3_bucket.upload.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "upload" {
  bucket = aws_s3_bucket.upload.id
  versioning_configuration {
    status = "Enabled" # a re-upload of the same key is a new version, and a new event
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "upload" {
  bucket = aws_s3_bucket.upload.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "upload" {
  bucket = aws_s3_bucket.upload.id

  # Raw transcripts may contain personal data: keep them only as long as reprocessing needs them.
  rule {
    id     = "expire-raw-transcripts"
    status = "Enabled"
    filter {
      prefix = "incoming/"
    }
    expiration {
      days = var.upload_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "upload" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.upload.arn, "${aws_s3_bucket.upload.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # Uploads must use the bucket's KMS key: no SSE-S3 override, no other key.
  statement {
    sid       = "DenyNonKmsEncryption"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.upload.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }

  statement {
    sid       = "DenyWrongKmsKey"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.upload.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [var.kms_key_arn]
    }
  }
}

resource "aws_s3_bucket_policy" "upload" {
  bucket = aws_s3_bucket.upload.id
  policy = data.aws_iam_policy_document.upload.json

  depends_on = [aws_s3_bucket_public_access_block.upload]
}

# Send all object events of this bucket to EventBridge (filtering happens in the rule below).
resource "aws_s3_bucket_notification" "upload" {
  bucket      = aws_s3_bucket.upload.id
  eventbridge = true
}

# ---------------------------------------------------------------- queues

resource "aws_sqs_queue" "jobs_dlq" {
  name                              = "${var.name_prefix}-jobs-dlq"
  message_retention_seconds         = 1209600 # 14 days, the maximum: time to investigate and redrive
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
}

resource "aws_sqs_queue" "jobs" {
  name = "${var.name_prefix}-jobs"
  # Must exceed the longest job (incl. retries inside the worker). The worker also extends visibility
  # with ChangeMessageVisibility while it is still making progress (heartbeat), so this is a ceiling.
  visibility_timeout_seconds        = var.visibility_timeout_seconds
  message_retention_seconds         = 345600 # 4 days
  receive_wait_time_seconds         = 20     # long polling
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "jobs_dlq" {
  queue_url = aws_sqs_queue.jobs_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.jobs.arn]
  })
}

resource "aws_sqs_queue" "review_dlq" {
  name                              = "${var.name_prefix}-review-dlq"
  message_retention_seconds         = 1209600
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
}

# One message per episode that has ⚠ claims: {job key, episode, report s3 uri, the ⚠ rows}.
# Consumed by a review UI (out of scope) or an Amazon A2I human loop.
resource "aws_sqs_queue" "review" {
  name                              = "${var.name_prefix}-review"
  visibility_timeout_seconds        = 300
  message_retention_seconds         = 1209600
  receive_wait_time_seconds         = 20
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.review_dlq.arn
    maxReceiveCount     = 5
  })
}

# ---------------------------------------------------------------- EventBridge rule

resource "aws_cloudwatch_event_rule" "transcript_uploaded" {
  name        = "${var.name_prefix}-transcript-uploaded"
  description = "New transcript in the upload bucket -> jobs queue"
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.upload.bucket] }
      object = { key = [for ext in var.accepted_extensions : { wildcard = "incoming/*.${ext}" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "jobs_queue" {
  rule      = aws_cloudwatch_event_rule.transcript_uploaded.name
  target_id = "jobs-queue"
  arn       = aws_sqs_queue.jobs.arn

  # Only what the worker needs; the event id doubles as a trace/correlation id.
  input_transformer {
    input_paths = {
      id      = "$.id"
      time    = "$.time"
      bucket  = "$.detail.bucket.name"
      key     = "$.detail.object.key"
      version = "$.detail.object.version-id"
      etag    = "$.detail.object.etag"
      size    = "$.detail.object.size"
    }
    input_template = <<-EOT
      {"event_id": "<id>", "time": "<time>", "bucket": "<bucket>", "key": "<key>", "version_id": "<version>", "etag": "<etag>", "size": <size>}
    EOT
  }

  retry_policy {
    maximum_event_age_in_seconds = 86400
    maximum_retry_attempts       = 185
  }

  # Events EventBridge cannot deliver (e.g. KMS or policy misconfiguration) land in the DLQ too.
  dead_letter_config {
    arn = aws_sqs_queue.jobs_dlq.arn
  }
}

data "aws_iam_policy_document" "jobs_queue" {
  statement {
    sid       = "AllowEventBridgeRule"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.jobs.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.transcript_uploaded.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "jobs" {
  queue_url = aws_sqs_queue.jobs.id
  policy    = data.aws_iam_policy_document.jobs_queue.json
}

data "aws_iam_policy_document" "jobs_dlq" {
  statement {
    sid       = "AllowEventBridgeDeadLetters"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.jobs_dlq.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.transcript_uploaded.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "jobs_dlq" {
  queue_url = aws_sqs_queue.jobs_dlq.id
  policy    = data.aws_iam_policy_document.jobs_dlq.json
}
