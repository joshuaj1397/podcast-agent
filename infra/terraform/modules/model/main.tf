terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

# SageMaker real-time endpoint for the LoRA-tuned task model (Evo² output), served by vLLM with multi-LoRA:
# one GPU holds the base weights once and serves several adapters (stable + canary task model, the evolver)
# selected per request. Disabled by default: the base path is Bedrock-only.
#
# Alternatives (see docs/deployment.md):
#  - Bedrock Custom Model Import of the merged weights, if it supports the Gemma 4 architecture
#    (serverless, scales to zero, billed per active model copy).
#  - SageMaker inference components (adapter-per-component, can scale to zero) or an async endpoint
#    (scale to zero on an empty backlog) when traffic is bursty.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  n             = var.enabled ? 1 : 0
  account_id    = data.aws_caller_identity.current.account_id
  region        = data.aws_region.current.region
  partition     = data.aws_partition.current.partition
  endpoint_name = "${var.name_prefix}-lora"
  variant_name  = "lora"

  # ECR repository of the serving image (an AWS LMI/vLLM deep learning container or our own build).
  image_match          = try(regex("^(\\d{12})\\.dkr\\.ecr\\.([a-z0-9-]+)\\.amazonaws\\.com/([^:@]+)", var.image_uri), null)
  image_repository_arn = local.image_match == null ? "*" : "arn:${local.partition}:ecr:${local.image_match[1]}:${local.image_match[0]}:repository/${local.image_match[2]}"

  # A new model version or instance setting creates a new model + endpoint config, and the endpoint
  # switches to it with a blue/green update (auto-rollback on the 5xx alarm below).
  config_hash = substr(sha1(jsonencode([var.model_version, var.image_uri, var.instance_type, var.min_instances, var.container_environment, var.data_capture_sampling_percentage])), 0, 8)
}

# ---------------------------------------------------------------- IAM

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "endpoint" {
  count              = local.n
  name               = "${var.name_prefix}-lora-endpoint"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "endpoint" {
  statement {
    sid       = "ReadModelArtifacts"
    actions   = ["s3:GetObject"]
    resources = ["${var.artifacts_bucket_arn}/models/*"]
  }
  statement {
    sid       = "ListModelArtifacts"
    actions   = ["s3:ListBucket"]
    resources = [var.artifacts_bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["models/*"]
    }
  }
  statement {
    sid       = "WriteDataCapture"
    actions   = ["s3:PutObject"]
    resources = ["${var.artifacts_bucket_arn}/data-capture/*"]
  }
  statement {
    sid       = "UseKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey", "kms:CreateGrant"]
    resources = [var.kms_key_arn]
  }
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "EcrPull"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    resources = [local.image_repository_arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/sagemaker/Endpoints/${local.endpoint_name}:*"]
  }
  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringLike"
      variable = "cloudwatch:namespace"
      values   = ["/aws/sagemaker/*"]
    }
  }
  statement {
    sid = "VpcAccess" # SageMaker creates ENIs in the private subnets; these EC2 APIs are not resource-scoped
    actions = [
      "ec2:CreateNetworkInterface", "ec2:CreateNetworkInterfacePermission", "ec2:DeleteNetworkInterface",
      "ec2:DeleteNetworkInterfacePermission", "ec2:DescribeNetworkInterfaces", "ec2:DescribeVpcs",
      "ec2:DescribeDhcpOptions", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "endpoint" {
  count  = local.n
  name   = "serve-lora-model"
  role   = aws_iam_role.endpoint[0].id
  policy = data.aws_iam_policy_document.endpoint.json
}

# ---------------------------------------------------------------- model, endpoint config, endpoint

resource "aws_sagemaker_model" "lora" {
  count                    = local.n
  name                     = "${var.name_prefix}-lora-${local.config_hash}"
  execution_role_arn       = aws_iam_role.endpoint[0].arn
  enable_network_isolation = true # the container gets no network; SageMaker stages the weights from S3

  primary_container {
    image       = var.image_uri
    mode        = "SingleModel"
    environment = var.container_environment

    # Uncompressed prefix: base weights at the root, LoRA adapters under adapters/<name>/.
    model_data_source {
      s3_data_source {
        s3_uri           = var.model_data_s3_uri
        s3_data_type     = "S3Prefix"
        compression_type = "None"
      }
    }
  }

  vpc_config {
    subnets            = var.subnet_ids
    security_group_ids = [var.security_group_id]
  }

  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = var.image_uri != null && var.model_data_s3_uri != null
      error_message = "image_uri and model_data_s3_uri are required when the LoRA endpoint is enabled."
    }
  }

  depends_on = [aws_iam_role_policy.endpoint]
}

resource "aws_sagemaker_endpoint_configuration" "lora" {
  count = local.n
  name  = "${var.name_prefix}-lora-${local.config_hash}"

  production_variants {
    variant_name                                      = local.variant_name
    model_name                                        = aws_sagemaker_model.lora[0].name
    instance_type                                     = var.instance_type
    initial_instance_count                            = var.min_instances
    container_startup_health_check_timeout_in_seconds = 900
    model_data_download_timeout_in_seconds            = 900
  }

  # No kms_key_arn: ml.g6 instances use local NVMe instance storage, for which SageMaker rejects a storage
  # KMS key (instance storage is hardware-encrypted). Artifacts and captured data stay CMK-encrypted in S3.

  dynamic "data_capture_config" {
    for_each = var.data_capture_sampling_percentage > 0 ? [1] : []
    content {
      enable_capture              = true
      initial_sampling_percentage = var.data_capture_sampling_percentage
      destination_s3_uri          = "s3://${var.artifacts_bucket_name}/data-capture/${local.endpoint_name}"
      kms_key_id                  = var.kms_key_arn

      capture_options {
        capture_mode = "Input"
      }
      capture_options {
        capture_mode = "Output"
      }
      capture_content_type_header {
        json_content_types = ["application/json"]
      }
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Blue/green update with a baking period: if the 5xx alarm fires, SageMaker rolls back to the old fleet.
resource "aws_cloudwatch_metric_alarm" "rollback" {
  count               = local.n
  alarm_name          = "${local.endpoint_name}-deploy-rollback"
  alarm_description   = "Invocation 5xx on the LoRA endpoint; used as the blue/green auto-rollback trigger."
  namespace           = "AWS/SageMaker"
  metric_name         = "Invocation5XXErrors"
  dimensions          = { EndpointName = local.endpoint_name, VariantName = local.variant_name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_sagemaker_endpoint" "lora" {
  count                = local.n
  name                 = local.endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.lora[0].name

  deployment_config {
    blue_green_update_policy {
      # ALL_AT_ONCE + baking period, because a single-instance fleet cannot be split into a canary.
      # With >= 2 instances use CANARY (CAPACITY_PERCENT). Per-job canaries happen upstream anyway:
      # the release flag routes a share of jobs to the candidate adapter on the same endpoint.
      traffic_routing_configuration {
        type                     = "ALL_AT_ONCE"
        wait_interval_in_seconds = 600
      }
      termination_wait_in_seconds          = 300
      maximum_execution_timeout_in_seconds = 3600
    }
    auto_rollback_configuration {
      alarms {
        alarm_name = aws_cloudwatch_metric_alarm.rollback[0].alarm_name
      }
    }
  }
}

# ---------------------------------------------------------------- autoscaling
# Real-time variants scale between min_instances (>= 1) and max_instances. Scale-to-zero needs
# inference components or an async endpoint (documented trade-off: cold start of minutes on a GPU).

resource "aws_appautoscaling_target" "lora" {
  count              = local.n
  service_namespace  = "sagemaker"
  resource_id        = "endpoint/${aws_sagemaker_endpoint.lora[0].name}/variant/${local.variant_name}"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  min_capacity       = var.min_instances
  max_capacity       = var.max_instances
}

resource "aws_appautoscaling_policy" "lora" {
  count              = local.n
  name               = "${local.endpoint_name}-invocations-per-instance"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.lora[0].service_namespace
  resource_id        = aws_appautoscaling_target.lora[0].resource_id
  scalable_dimension = aws_appautoscaling_target.lora[0].scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.target_invocations_per_instance
    scale_out_cooldown = 120
    scale_in_cooldown  = 600

    predefined_metric_specification {
      predefined_metric_type = "SageMakerVariantInvocationsPerInstance"
    }
  }
}
