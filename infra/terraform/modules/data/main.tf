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
  account_id      = data.aws_caller_identity.current.account_id
  region          = data.aws_region.current.region
  partition       = data.aws_partition.current.partition
  collection_name = "${var.name_prefix}-kb"
}

# ---------------------------------------------------------------- KMS (one CMK for this environment's data plane)
# Encrypts buckets, queues, the jobs table, log groups, the alert topic and the OpenSearch collection.
# Per-client keys for tenants that require them: see docs/deployment.md (multi-tenancy).

data "aws_iam_policy_document" "kms" {
  statement {
    sid       = "EnableIamPolicies"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid       = "CloudWatchLogs"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${local.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:*"]
    }
  }

  # EventBridge -> encrypted SQS queue, CloudWatch alarms / EventBridge / Budgets -> encrypted SNS topic.
  statement {
    sid       = "AwsServicesPublishingToEncryptedQueuesAndTopics"
    actions   = ["kms:GenerateDataKey*", "kms:Decrypt"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "cloudwatch.amazonaws.com", "budgets.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_kms_key" "this" {
  description             = "${var.name_prefix} data plane (transcripts, reports, queues, jobs table, logs)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms.json
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name_prefix}"
  target_key_id = aws_kms_key.this.key_id
}

# ---------------------------------------------------------------- buckets
# results: report.json / report.md / trace.jsonl per job, under results/<client>/<episode>/<idempotency-key>/
# harness: the harness registry. Versioned; harness/<version>.yaml is immutable once released,
#          candidates/ holds Evo² proposals awaiting the eval gate.

locals {
  buckets = {
    results = "${var.name_prefix}-results-${local.account_id}"
    harness = "${var.name_prefix}-harness-${local.account_id}"
  }
}

resource "aws_s3_bucket" "this" {
  for_each      = local.buckets
  bucket        = each.value
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.this.arn
    }
    bucket_key_enabled = true # cuts KMS request costs
  }
}

data "aws_iam_policy_document" "tls_only" {
  for_each = aws_s3_bucket.this

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]
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

resource "aws_s3_bucket_policy" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  policy   = data.aws_iam_policy_document.tls_only[each.key].json

  depends_on = [aws_s3_bucket_public_access_block.this]
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.this["results"].id

  rule {
    id     = "reports-to-infrequent-access"
    status = "Enabled"
    filter {
      prefix = "results/"
    }
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  # The worker tags trace.jsonl objects kind=trace: they contain transcript excerpts, so they expire sooner.
  rule {
    id     = "expire-traces"
    status = "Enabled"
    filter {
      tag {
        key   = "kind"
        value = "trace"
      }
    }
    expiration {
      days = var.trace_retention_days
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "harness" {
  bucket = aws_s3_bucket.this["harness"].id

  # Released harness versions are kept forever (they are tiny); rejected candidates are cleaned up.
  rule {
    id     = "expire-rejected-candidates"
    status = "Enabled"
    filter {
      prefix = "candidates/"
    }
    expiration {
      days = 90
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# ---------------------------------------------------------------- jobs table
# Single-table design:
#   pk = JOB#<idempotency_key>  sk = META         status, source s3 uri, client_id, harness/model version,
#                                                 lease_owner + lease_expires_at, attempts, result prefix
#   pk = JOB#<idempotency_key>  sk = STEP#<name>  step output (or an S3 pointer if > 300 KB) -> retries resume here
#   pk = CLIENT#<client_id>     sk = QUOTA#<yyyy-mm>  per-client usage counters
# idempotency_key = sha256(transcript bytes + harness version + model version)

resource "aws_dynamodb_table" "jobs" {
  name                        = "${var.name_prefix}-jobs"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "status"
    type = "S"
  }
  attribute {
    name = "updated_at"
    type = "S"
  }

  # Operational queries: "all FAILED / NEEDS_REVIEW jobs since <time>".
  global_secondary_index {
    name               = "by-status"
    projection_type    = "INCLUDE"
    non_key_attributes = ["episode_id", "client_id", "harness_version", "model_version", "attempts", "error"]

    key_schema {
      attribute_name = "status"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "updated_at"
      key_type       = "RANGE"
    }
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.this.arn
  }
}

# ---------------------------------------------------------------- release flags
# Which harness + model version workers use. The promotion pipeline (not Terraform) owns the value after creation:
#   {"stable": {...}, "canary": {...} | null, "canary_percent": 0-100}
# Workers route a job to the canary when hash(idempotency_key) % 100 < canary_percent. Rollback = set canary_percent 0.

resource "aws_ssm_parameter" "release" {
  name        = "/${var.name_prefix}/release"
  description = "Active harness/model versions and canary split for the podcast agent workers"
  type        = "String"
  tier        = "Standard"
  value = jsonencode({
    stable         = { harness = var.initial_harness_version, model = var.initial_model }
    canary         = null
    canary_percent = 0
  })

  lifecycle {
    ignore_changes = [value]
  }
}

# ---------------------------------------------------------------- OpenSearch Serverless (BM25 knowledge base)
# A SEARCH collection; BM25 is OpenSearch's default similarity, so the in-process rank_bm25 index maps 1:1.
# Reachable only through its own VPC endpoint. Cost floor: the minimum OCUs (see docs/deployment.md, Cost).

resource "aws_opensearchserverless_security_policy" "encryption" {
  count = var.enable_opensearch ? 1 : 0

  name        = "${var.name_prefix}-kb-enc"
  type        = "encryption"
  description = "Encrypt the KB collection with the environment CMK"
  policy = jsonencode({
    Rules       = [{ ResourceType = "collection", Resource = ["collection/${local.collection_name}"] }]
    AWSOwnedKey = false
    KmsARN      = aws_kms_key.this.arn
  })
}

resource "aws_opensearchserverless_vpc_endpoint" "kb" {
  count = var.enable_opensearch ? 1 : 0

  name               = "${var.name_prefix}-kb"
  vpc_id             = var.vpc_id
  subnet_ids         = var.subnet_ids
  security_group_ids = [var.endpoint_security_group_id]
}

resource "aws_opensearchserverless_security_policy" "network" {
  count = var.enable_opensearch ? 1 : 0

  name        = "${var.name_prefix}-kb-net"
  type        = "network"
  description = "KB collection: VPC endpoint access only"
  policy = jsonencode([{
    Description = "Private access from the agent VPC"
    Rules = [
      { ResourceType = "collection", Resource = ["collection/${local.collection_name}"] },
      { ResourceType = "dashboard", Resource = ["collection/${local.collection_name}"] },
    ]
    AllowFromPublic = false
    SourceVPCEs     = [aws_opensearchserverless_vpc_endpoint.kb[0].id]
  }])
}

resource "aws_opensearchserverless_collection" "kb" {
  count = var.enable_opensearch ? 1 : 0

  name             = local.collection_name
  type             = "SEARCH"
  description      = "Fact-checking knowledge base (BM25)"
  standby_replicas = var.opensearch_standby_replicas

  depends_on = [
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
  ]
}

resource "aws_opensearchserverless_access_policy" "kb" {
  count = var.enable_opensearch ? 1 : 0

  name        = "${var.name_prefix}-kb-access"
  type        = "data"
  description = "Workers read the KB; the KB loader (CI) writes it"
  policy = jsonencode(concat(
    [{
      Description = "Agent workers: read-only"
      Principal   = var.opensearch_reader_arns
      Rules = [{
        ResourceType = "index"
        Resource     = ["index/${local.collection_name}/*"]
        Permission   = ["aoss:ReadDocument", "aoss:DescribeIndex"]
      }]
    }],
    length(var.opensearch_writer_arns) == 0 ? [] : [{
      Description = "KB loader: manage indexes and documents"
      Principal   = var.opensearch_writer_arns
      Rules = [
        {
          ResourceType = "index"
          Resource     = ["index/${local.collection_name}/*"]
          Permission   = ["aoss:CreateIndex", "aoss:UpdateIndex", "aoss:DescribeIndex", "aoss:ReadDocument", "aoss:WriteDocument"]
        },
        {
          ResourceType = "collection"
          Resource     = ["collection/${local.collection_name}"]
          Permission   = ["aoss:DescribeCollectionItems", "aoss:CreateCollectionItems", "aoss:UpdateCollectionItems"]
        },
      ]
    }],
  ))
}
