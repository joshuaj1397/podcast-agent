terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

# Infrastructure for the Evo² improvement loop. Terraform owns the long-lived pieces only:
#   - the ML artifacts bucket (datasets/, checkpoints/, models/, evo-runs/, data-capture/)
#   - the IAM role SageMaker training/processing jobs run as
#   - SageMaker Model Package Groups (model registry) for the task model and the evolver
# The jobs themselves (Evo² search rounds, SFT/GRPO LoRA training on managed spot GPUs with
# checkpointing to S3, the held-out eval gate) are launched per round by the promotion pipeline
# (SageMaker Python SDK / SageMaker Pipelines), not by Terraform.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition
}

# ---------------------------------------------------------------- artifacts bucket

resource "aws_s3_bucket" "artifacts" {
  bucket        = "${var.name_prefix}-ml-artifacts-${local.account_id}"
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled" # model artifacts referenced by registered model packages must not change underneath
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  # Spot checkpoints only matter until the job finishes.
  rule {
    id     = "expire-checkpoints"
    status = "Enabled"
    filter {
      prefix = "checkpoints/"
    }
    expiration {
      days = var.checkpoint_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  # Captured endpoint traffic contains transcript text.
  rule {
    id     = "expire-data-capture"
    status = "Enabled"
    filter {
      prefix = "data-capture/"
    }
    expiration {
      days = 30
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 3
    }
  }
}

data "aws_iam_policy_document" "artifacts" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
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
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = data.aws_iam_policy_document.artifacts.json

  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}

# ---------------------------------------------------------------- IAM role for training / processing jobs

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

resource "aws_iam_role" "training" {
  name               = "${var.name_prefix}-training"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "training" {
  statement {
    sid       = "ArtifactsReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }
  statement {
    sid       = "ArtifactsList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.artifacts.arn]
  }

  # Evo² reads released harness versions and writes candidates; promotion to harness/ is the pipeline's job.
  statement {
    sid       = "HarnessRegistryRead"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${var.harness_bucket_arn}/*"]
  }
  statement {
    sid       = "HarnessCandidatesWrite"
    actions   = ["s3:PutObject"]
    resources = ["${var.harness_bucket_arn}/candidates/*"]
  }
  statement {
    sid       = "HarnessRegistryList"
    actions   = ["s3:ListBucket"]
    resources = [var.harness_bucket_arn]
  }

  # Evo² search runs the agent: base model on Bedrock, evolver/task adapters on the LoRA endpoint.
  statement {
    sid       = "InvokeModels"
    actions   = ["bedrock:InvokeModel", "sagemaker:InvokeEndpoint"]
    resources = [var.bedrock_model_arn, "arn:${local.partition}:sagemaker:${local.region}:${local.account_id}:endpoint/${var.name_prefix}-*"]
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
    sid       = "EcrPull" # AWS deep learning containers + our own training image repos
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    resources = concat(var.training_image_repository_arns, ["arn:${local.partition}:ecr:${local.region}:${local.account_id}:repository/${var.name_prefix}/*"])
  }

  statement {
    sid     = "Logs"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = [
      "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/sagemaker/TrainingJobs:*",
      "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/sagemaker/ProcessingJobs:*",
    ]
  }
  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringLike"
      variable = "cloudwatch:namespace"
      values   = ["/aws/sagemaker/*", "PodcastAgent/Evo"]
    }
  }

  statement {
    sid = "VpcAccess" # jobs run in the private subnets (network isolation for training containers)
    actions = [
      "ec2:CreateNetworkInterface", "ec2:CreateNetworkInterfacePermission", "ec2:DeleteNetworkInterface",
      "ec2:DeleteNetworkInterfacePermission", "ec2:DescribeNetworkInterfaces", "ec2:DescribeVpcs",
      "ec2:DescribeDhcpOptions", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "training" {
  name   = "evo-training"
  role   = aws_iam_role.training.id
  policy = data.aws_iam_policy_document.training.json
}

# ---------------------------------------------------------------- model registry
# Each Evo² round registers model packages as PendingManualApproval with the eval report (held-out V̂,
# per-metric deltas vs. the current stable) attached as model metrics. The promotion pipeline flips
# them to Approved only if the eval gate passes; approval is what the deploy step consumes.

resource "aws_sagemaker_model_package_group" "this" {
  for_each = {
    task-model = "LoRA adapters for the podcast agent's task model (summarize / notes / claims / judge)"
    evolver    = "LoRA adapters for the Evo² evolver (Proposer + Reflector roles)"
  }

  model_package_group_name        = "${var.name_prefix}-${each.key}"
  model_package_group_description = each.value
}
