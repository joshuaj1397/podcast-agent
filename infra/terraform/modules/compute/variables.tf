variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "environment" {
  description = "Environment name (dev, stage, prod); exported as an OpenTelemetry resource attribute."
  type        = string
}

# ---- network

variable "subnet_ids" {
  description = "Private subnets for the worker tasks."
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group for the worker tasks."
  type        = string
}

# ---- data plane wiring

variable "kms_key_arn" {
  description = "Environment CMK (ECR, logs, secret; decrypt for S3/SQS/DynamoDB)."
  type        = string
}

variable "upload_bucket_arn" {
  description = "Upload bucket ARN (read incoming/*)."
  type        = string
}

variable "results_bucket_name" {
  description = "Results bucket name."
  type        = string
}

variable "results_bucket_arn" {
  description = "Results bucket ARN (write results/*)."
  type        = string
}

variable "harness_bucket_name" {
  description = "Harness registry bucket name."
  type        = string
}

variable "harness_bucket_arn" {
  description = "Harness registry bucket ARN (read harness/* and kb/*)."
  type        = string
}

variable "jobs_table_name" {
  description = "DynamoDB jobs table name."
  type        = string
}

variable "jobs_table_arn" {
  description = "DynamoDB jobs table ARN."
  type        = string
}

variable "jobs_queue_url" {
  description = "Jobs queue URL."
  type        = string
}

variable "jobs_queue_arn" {
  description = "Jobs queue ARN."
  type        = string
}

variable "jobs_queue_name" {
  description = "Jobs queue name (scaling metric dimension)."
  type        = string
}

variable "review_queue_url" {
  description = "Human review queue URL."
  type        = string
}

variable "review_queue_arn" {
  description = "Human review queue ARN."
  type        = string
}

variable "release_parameter_name" {
  description = "SSM release parameter name."
  type        = string
}

variable "release_parameter_arn" {
  description = "SSM release parameter ARN."
  type        = string
}

# ---- models and KB

variable "bedrock_model_id" {
  description = "Bedrock model id of the base model (verify against the Bedrock catalog in the target region)."
  type        = string
}

variable "enable_sagemaker_endpoint" {
  description = "Whether the LoRA task-model endpoint exists (adds it to the model chain and IAM)."
  type        = bool
  default     = false
}

variable "sagemaker_endpoint_name" {
  description = "Name of the LoRA task-model endpoint (null when disabled)."
  type        = string
  default     = null
}

variable "enable_opensearch" {
  description = "Whether the OpenSearch Serverless KB collection exists."
  type        = bool
  default     = true
}

variable "opensearch_collection_arn" {
  description = "KB collection ARN (null when disabled)."
  type        = string
  default     = null
}

variable "opensearch_collection_endpoint" {
  description = "KB collection endpoint (null when disabled)."
  type        = string
  default     = null
}

# ---- task sizing and scaling

variable "image_tag" {
  description = "Agent image tag (the git SHA; CI passes it on every deploy)."
  type        = string
  default     = "bootstrap"
}

variable "adot_image_tag" {
  description = "AWS Distro for OpenTelemetry collector tag. Pin to a released tag from gallery.ecr.aws/aws-observability/aws-otel-collector."
  type        = string
  default     = "v0.43.3"
}

variable "task_cpu" {
  description = "Fargate task CPU units. The agent is I/O bound (waits on model calls), so 1 vCPU serves several concurrent jobs."
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate task memory (MiB)."
  type        = number
  default     = 2048
}

variable "cpu_architecture" {
  description = "X86_64 or ARM64 (Graviton is ~20% cheaper; needs a multi-arch image build)."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "cpu_architecture must be X86_64 or ARM64."
  }
}

variable "worker_concurrency" {
  description = "Episodes processed concurrently by one worker task (asyncio)."
  type        = number
  default     = 2
}

variable "min_workers" {
  description = "Minimum worker tasks. 0 = scale to zero when the queue is empty."
  type        = number
  default     = 0
}

variable "max_workers" {
  description = "Maximum worker tasks. Size it against the model quota (Bedrock tokens/min, endpoint capacity), not just Fargate."
  type        = number
  default     = 10
}

variable "target_backlog_per_task" {
  description = "Target (waiting + in-flight) jobs per running worker. ~worker_concurrency x (acceptable wait / job duration)."
  type        = number
  default     = 4
}

variable "fargate_spot_weight" {
  description = "Weight of FARGATE_SPOT vs FARGATE (weight 1) in the capacity provider strategy. 0 disables Spot."
  type        = number
  default     = 3
}

variable "pii_redaction" {
  description = "PII redaction before LLM calls and logging: local (in-container regex/Presidio, ~free) or comprehend (Amazon Comprehend DetectPiiEntities; needs the comprehend VPC endpoint, billed per 100 characters)."
  type        = string
  default     = "local"

  validation {
    condition     = contains(["local", "comprehend"], var.pii_redaction)
    error_message = "pii_redaction must be local or comprehend."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the worker log group."
  type        = number
  default     = 30
}
