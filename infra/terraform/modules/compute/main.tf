terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition

  cluster_name = "${var.name_prefix}-cluster"
  service_name = "${var.name_prefix}-worker"

  # ADOT collector image through an ECR pull-through cache of public.ecr.aws: the tasks have no internet
  # egress, but ECR fetches upstream on their behalf and serves the image through the ecr.* VPC endpoints.
  pull_through_prefix = "${var.name_prefix}-pub"
  adot_image          = "${local.account_id}.dkr.ecr.${local.region}.amazonaws.com/${local.pull_through_prefix}/aws-observability/aws-otel-collector:${var.adot_image_tag}"

  bedrock_model_arn      = "arn:${local.partition}:bedrock:${local.region}::foundation-model/${var.bedrock_model_id}"
  sagemaker_endpoint_arn = "arn:${local.partition}:sagemaker:${local.region}:${local.account_id}:endpoint/${coalesce(var.sagemaker_endpoint_name, "none")}"

  # Model fallback chain (LiteLLM model strings): LoRA task model on SageMaker -> base model on Bedrock.
  # After the chain is exhausted the pipeline's deterministic fallbacks take over (report.degraded).
  model_chain = concat(
    var.enable_sagemaker_endpoint ? ["sagemaker_chat/${var.sagemaker_endpoint_name}"] : [],
    ["bedrock/${var.bedrock_model_id}"],
  )

  worker_env = {
    AWS_REGION                  = local.region
    PODCAST_MODEL               = local.model_chain[0]
    PODCAST_FALLBACK_MODELS     = join(",", slice(local.model_chain, 1, length(local.model_chain)))
    PODCAST_RELEASE_PARAMETER   = var.release_parameter_name # stable/canary harness + model versions
    PODCAST_HARNESS_BUCKET      = var.harness_bucket_name
    PODCAST_RESULTS_BUCKET      = var.results_bucket_name
    PODCAST_JOBS_TABLE          = var.jobs_table_name
    PODCAST_JOBS_QUEUE_URL      = var.jobs_queue_url
    PODCAST_REVIEW_QUEUE_URL    = var.review_queue_url
    PODCAST_KB_BACKEND          = var.enable_opensearch ? "opensearch" : "local"
    PODCAST_OPENSEARCH_ENDPOINT = coalesce(var.opensearch_collection_endpoint, "none")
    PODCAST_KB_INDEX            = "kb-facts"
    PODCAST_WEB_SEARCH          = "mock" # "provider" + PODCAST_SEARCH_API_SECRET_ARN needs enable_nat_gateway
    PODCAST_SEARCH_API_SECRET   = aws_secretsmanager_secret.search_api.arn
    PODCAST_PII_REDACTION       = var.pii_redaction
    PODCAST_WORKER_CONCURRENCY  = tostring(var.worker_concurrency)
    PODCAST_LOG_FORMAT          = "json" # trace.jsonl events also go to stdout -> CloudWatch Logs Insights
    OTEL_SERVICE_NAME           = "podcast-agent-worker"
    OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4317"
    OTEL_RESOURCE_ATTRIBUTES    = "deployment.environment=${var.environment}"
  }

  adot_config = yamlencode({
    receivers = {
      otlp = { protocols = { grpc = { endpoint = "localhost:4317" }, http = { endpoint = "localhost:4318" } } }
    }
    processors = { batch = { timeout = "10s" } }
    exporters = {
      awsxray = { region = local.region }
      awsemf = {
        region          = local.region
        namespace       = "PodcastAgent"
        log_group_name  = aws_cloudwatch_log_group.worker.name
        log_stream_name = "otel-emf/{TaskId}"
      }
    }
    service = {
      pipelines = {
        traces  = { receivers = ["otlp"], processors = ["batch"], exporters = ["awsxray"] }
        metrics = { receivers = ["otlp"], processors = ["batch"], exporters = ["awsemf"] }
      }
    }
  })
}

# ---------------------------------------------------------------- ECR

resource "aws_ecr_repository" "agent" {
  name                 = "${var.name_prefix}/agent"
  image_tag_mutability = "IMMUTABLE" # tags are git SHAs; a tag always means the same image

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }
}

resource "aws_ecr_lifecycle_policy" "agent" {
  repository = aws_ecr_repository.agent.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 7 days"
        selection    = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 7 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the last 50 images (rollback window)"
        selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 50 }
        action       = { type = "expire" }
      },
    ]
  })
}

resource "aws_ecr_pull_through_cache_rule" "ecr_public" {
  ecr_repository_prefix = local.pull_through_prefix
  upstream_registry_url = "public.ecr.aws"
}

# ---------------------------------------------------------------- logs

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name_prefix}/worker"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

# Second line of defence behind the in-app redaction: CloudWatch masks these identifiers in the console/API.
resource "aws_cloudwatch_log_data_protection_policy" "worker" {
  log_group_name = aws_cloudwatch_log_group.worker.name
  policy_document = jsonencode({
    Name    = "${var.name_prefix}-worker-pii"
    Version = "2021-06-01"
    Statement = [
      {
        Sid            = "audit"
        DataIdentifier = local.pii_identifiers
        Operation      = { Audit = { FindingsDestination = {} } }
      },
      {
        Sid            = "redact"
        DataIdentifier = local.pii_identifiers
        Operation      = { Deidentify = { MaskConfig = {} } }
      },
    ]
  })
}

locals {
  pii_identifiers = [
    for id in ["EmailAddress", "PhoneNumber-US", "CreditCardNumber", "IpAddress", "AwsSecretKey"] :
    "arn:${local.partition}:dataprotection::aws:data-identifier/${id}"
  ]
}

# ---------------------------------------------------------------- secrets

# Only needed when PODCAST_WEB_SEARCH=provider. The value is set out of band (never in Terraform state),
# and the worker reads it lazily, so an empty secret does not block task start-up.
resource "aws_secretsmanager_secret" "search_api" {
  name                    = "${var.name_prefix}/search-api-key"
  description             = "API key for the external web-search provider used by the fact-check researcher"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = 7
}

# ---------------------------------------------------------------- IAM

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:ecs:${local.region}:${local.account_id}:*"]
    }
  }
}

# Execution role: used by ECS itself to pull images and ship logs.
resource "aws_iam_role" "execution" {
  name               = "${var.name_prefix}-worker-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "execution" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # does not support resource-level permissions
  }
  statement {
    sid       = "EcrPull"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    resources = [aws_ecr_repository.agent.arn, "arn:${local.partition}:ecr:${local.region}:${local.account_id}:repository/${local.pull_through_prefix}/*"]
  }
  statement {
    sid       = "EcrPullThroughCache"
    actions   = ["ecr:BatchImportUpstreamImage", "ecr:CreateRepository"]
    resources = ["arn:${local.partition}:ecr:${local.region}:${local.account_id}:repository/${local.pull_through_prefix}/*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.worker.arn}:*"]
  }
  statement {
    sid       = "DecryptImages"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "pull-and-log"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

# Task role: what the agent code may do. Least privilege, one statement per dependency.
resource "aws_iam_role" "task" {
  name               = "${var.name_prefix}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "ConsumeJobs"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
    resources = [var.jobs_queue_arn]
  }

  statement {
    sid       = "SendForReview"
    actions   = ["sqs:SendMessage"]
    resources = [var.review_queue_arn]
  }

  statement {
    sid       = "ReadTranscripts"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${var.upload_bucket_arn}/incoming/*"]
  }

  statement {
    sid       = "ReadHarnessRegistry"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${var.harness_bucket_arn}/harness/*", "${var.harness_bucket_arn}/kb/*"]
  }

  statement {
    sid       = "WriteResults"
    actions   = ["s3:PutObject", "s3:PutObjectTagging", "s3:GetObject"]
    resources = ["${var.results_bucket_arn}/results/*"]
  }

  statement {
    sid       = "ListResults" # lets HeadObject return 404 (not 403) when checking for an existing report
    actions   = ["s3:ListBucket"]
    resources = [var.results_bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["results/*"]
    }
  }

  statement {
    sid = "JobsTable"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
      "dynamodb:Query", "dynamodb:ConditionCheckItem",
    ]
    resources = [var.jobs_table_arn, "${var.jobs_table_arn}/index/*"]
  }

  statement {
    sid       = "ReadReleaseFlags"
    actions   = ["ssm:GetParameter"]
    resources = [var.release_parameter_arn]
  }

  statement {
    sid       = "InvokeBaseModel"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"] # Converse uses InvokeModel
    resources = [local.bedrock_model_arn]
  }

  dynamic "statement" {
    for_each = var.enable_sagemaker_endpoint ? [1] : []
    content {
      sid       = "InvokeLoraEndpoint"
      actions   = ["sagemaker:InvokeEndpoint"]
      resources = [local.sagemaker_endpoint_arn]
    }
  }

  dynamic "statement" {
    for_each = var.enable_opensearch ? [1] : []
    content {
      sid       = "SearchKnowledgeBase" # the collection's data access policy narrows this to read-only
      actions   = ["aoss:APIAccessAll"]
      resources = [var.opensearch_collection_arn]
    }
  }

  statement {
    sid       = "UseDataKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values = [for svc in ["s3", "sqs", "dynamodb", "secretsmanager"] :
      "${svc}.${local.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "ReadSearchApiKey"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.search_api.arn]
  }

  dynamic "statement" {
    for_each = var.pii_redaction == "comprehend" ? [1] : []
    content {
      sid       = "RedactPii"
      actions   = ["comprehend:DetectPiiEntities"]
      resources = ["*"] # Comprehend's real-time APIs do not support resource-level permissions
    }
  }

  statement {
    sid       = "Traces"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }

  statement {
    sid       = "EmfMetrics" # ADOT awsemf exporter writes into the worker log group
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = [aws_cloudwatch_log_group.worker.arn, "${aws_cloudwatch_log_group.worker.arn}:*"]
  }

  # A worker protects itself from scale-in while it holds a job (ECS task scale-in protection).
  statement {
    sid       = "TaskScaleInProtection"
    actions   = ["ecs:UpdateTaskProtection", "ecs:GetTaskProtection"]
    resources = ["arn:${local.partition}:ecs:${local.region}:${local.account_id}:task/${local.cluster_name}/*"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "podcast-agent-worker"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ---------------------------------------------------------------- ECS

resource "aws_ecs_cluster" "this" {
  name = local.cluster_name

  setting {
    name  = "containerInsights"
    value = "enabled" # also provides RunningTaskCount for the backlog-per-task scaling metric
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name_prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  # Writable scratch space; the root filesystem is read-only. The paths exist in the image (owned by the
  # non-root app user), so Fargate copies their ownership onto the volumes.
  volume {
    name = "out"
  }
  volume {
    name = "cache"
  }
  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = "${aws_ecr_repository.agent.repository_url}:${var.image_tag}"
      essential = true
      # `podcast-agent worker`: SQS long-poll loop around Pipeline.run_file (idempotency check, step
      # checkpoints, visibility heartbeat, upload of report.json/report.md/trace.jsonl, review message).
      command                = ["worker"]
      user                   = "1000"
      readonlyRootFilesystem = true
      stopTimeout            = 120 # SIGTERM -> stop polling, checkpoint, release the message; Fargate max is 120 s
      environment            = [for k in sort(keys(local.worker_env)) : { name = k, value = local.worker_env[k] }]
      mountPoints = [
        { sourceVolume = "out", containerPath = "/app/out", readOnly = false },
        { sourceVolume = "cache", containerPath = "/app/.cache", readOnly = false },
        { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false },
      ]
      linuxParameters = { initProcessEnabled = true }
      dependsOn       = [{ containerName = "otel-collector", condition = "START" }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.worker.name
          awslogs-region        = local.region
          awslogs-stream-prefix = "worker"
          mode                  = "non-blocking"
          max-buffer-size       = "25m"
        }
      }
    },
    {
      name              = "otel-collector"
      image             = local.adot_image
      essential         = false # telemetry is best effort; never kill a job because the collector died
      memoryReservation = 128
      environment       = [{ name = "AOT_CONFIG_CONTENT", value = local.adot_config }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.worker.name
          awslogs-region        = local.region
          awslogs-stream-prefix = "otel"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "worker" {
  name                   = local.service_name
  cluster                = aws_ecs_cluster.this.id
  task_definition        = aws_ecs_task_definition.worker.arn
  desired_count          = var.min_workers
  enable_execute_command = false
  propagate_tags         = "SERVICE"

  # Rolling deploy: new tasks start before old ones stop; old tasks get SIGTERM and drain (see stopTimeout).
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # Spot is safe here: an interrupted job is redelivered by SQS and resumes from its DynamoDB checkpoint.
  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = var.fargate_spot_weight
  }
  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = false
  }

  lifecycle {
    ignore_changes = [desired_count] # owned by Application Auto Scaling
  }

  depends_on = [aws_ecs_cluster_capacity_providers.this, aws_iam_role_policy.task]
}

# ---------------------------------------------------------------- autoscaling on queue backlog

resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.min_workers
  max_capacity       = var.max_workers
}

# 1..N: target tracking on (waiting + in-flight jobs) / running tasks. Counting in-flight jobs stops the
# policy from scaling in a worker that is still busy with the last messages.
resource "aws_appautoscaling_policy" "backlog_per_task" {
  name               = "${var.name_prefix}-backlog-per-task"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.target_backlog_per_task
    scale_out_cooldown = 60
    scale_in_cooldown  = 300

    customized_metric_specification {
      metrics {
        id          = "visible"
        label       = "Jobs waiting"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesVisible"
            dimensions {
              name  = "QueueName"
              value = var.jobs_queue_name
            }
          }
        }
      }
      metrics {
        id          = "inflight"
        label       = "Jobs in progress"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesNotVisible"
            dimensions {
              name  = "QueueName"
              value = var.jobs_queue_name
            }
          }
        }
      }
      metrics {
        id          = "tasks"
        label       = "Running workers"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "ECS/ContainerInsights"
            metric_name = "RunningTaskCount"
            dimensions {
              name  = "ClusterName"
              value = aws_ecs_cluster.this.name
            }
            dimensions {
              name  = "ServiceName"
              value = aws_ecs_service.worker.name
            }
          }
        }
      }
      metrics {
        id          = "backlog"
        label       = "Jobs per running worker"
        expression  = "(visible + inflight) / tasks"
        return_data = true
      }
    }
  }
}

# 0 -> 1: target tracking cannot scale out from zero tasks (the metric divides by zero), so a step policy
# wakes the service when jobs are waiting and nothing runs. Target tracking takes over from there.
resource "aws_appautoscaling_policy" "wake_from_zero" {
  name               = "${var.name_prefix}-wake-from-zero"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_lower_bound = 0
      scaling_adjustment          = max(var.min_workers, 1)
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "wake_from_zero" {
  alarm_name          = "${var.name_prefix}-jobs-waiting-no-workers"
  alarm_description   = "Jobs are queued but no worker runs: scale the worker service up from zero."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.wake_from_zero.arn]

  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      dimensions  = { QueueName = var.jobs_queue_name }
      period      = 60
      stat        = "Maximum"
    }
  }

  metric_query {
    id = "tasks"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "RunningTaskCount"
      dimensions  = { ClusterName = aws_ecs_cluster.this.name, ServiceName = aws_ecs_service.worker.name }
      period      = 60
      stat        = "Maximum"
    }
  }

  metric_query {
    id          = "wake"
    label       = "Jobs waiting with zero workers"
    expression  = "IF(visible > 0 AND FILL(tasks, 0) == 0, 1, 0)"
    return_data = true
  }
}
