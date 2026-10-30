variable "region" {
  description = "AWS region. Pick one where the Bedrock base model is available."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name; first part of every resource name and the project tag."
  type        = string
  default     = "podcast-agent"
}

variable "environment" {
  description = "Environment name: dev, stage or prod."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "stage", "prod"], var.environment)
    error_message = "environment must be dev, stage or prod."
  }
}

variable "cost_center" {
  description = "Cost center tag."
  type        = string
  default     = "ad-ops-ai"
}

variable "force_destroy" {
  description = "Allow destroying non-empty buckets (dev only)."
  type        = bool
  default     = true
}

# ---- network

variable "vpc_cidr" {
  description = "VPC CIDR."
  type        = string
  default     = "10.40.0.0/16"
}

variable "az_count" {
  description = "Number of AZs."
  type        = number
  default     = 2
}

variable "enable_nat_gateway" {
  description = "Add a NAT gateway (only needed for a real external web-search API)."
  type        = bool
  default     = false
}

# ---- models

variable "bedrock_model_id" {
  description = "Bedrock model id of the base model. Placeholder for Gemma 4 E2B: check the Bedrock catalog in the target region."
  type        = string
  default     = "google.gemma-4-e2b"
}

variable "enable_lora_endpoint" {
  description = "Deploy the SageMaker LoRA task-model endpoint (GPU, billed per hour while running)."
  type        = bool
  default     = false
}

variable "lora_image_uri" {
  description = "Serving image for the LoRA endpoint (LMI/vLLM container)."
  type        = string
  default     = null
}

variable "lora_model_data_s3_uri" {
  description = "S3 prefix with the base weights and adapters/ (in the ML artifacts bucket, under models/)."
  type        = string
  default     = null
}

variable "lora_model_version" {
  description = "Model registry version label being served."
  type        = string
  default     = "r1"
}

variable "lora_instance_type" {
  description = "LoRA endpoint instance type."
  type        = string
  default     = "ml.g6.xlarge"
}

variable "lora_data_capture_percentage" {
  description = "Share of endpoint traffic captured for monitoring/training (0 = off)."
  type        = number
  default     = 0
}

# ---- knowledge base

variable "enable_opensearch" {
  description = "Use OpenSearch Serverless for the KB (false = in-process BM25 over a KB snapshot in S3)."
  type        = bool
  default     = true
}

variable "kb_writer_arns" {
  description = "Principals allowed to (re)load the KB indexes, e.g. the CI deploy role."
  type        = list(string)
  default     = []
}

# ---- workers

variable "image_tag" {
  description = "Agent image tag (git SHA), set by CI."
  type        = string
  default     = "bootstrap"
}

variable "worker_cpu" {
  description = "Worker task CPU units."
  type        = number
  default     = 1024
}

variable "worker_memory" {
  description = "Worker task memory (MiB)."
  type        = number
  default     = 2048
}

variable "worker_concurrency" {
  description = "Concurrent episodes per worker."
  type        = number
  default     = 2
}

variable "min_workers" {
  description = "Minimum workers (0 = scale to zero)."
  type        = number
  default     = 0
}

variable "max_workers" {
  description = "Maximum workers."
  type        = number
  default     = 5
}

variable "target_backlog_per_task" {
  description = "Target jobs (waiting + in progress) per worker."
  type        = number
  default     = 4
}

variable "job_visibility_timeout_seconds" {
  description = "Jobs queue visibility timeout (> max job duration)."
  type        = number
  default     = 900
}

variable "max_receive_count" {
  description = "Attempts before a job is dead-lettered."
  type        = number
  default     = 3
}

# ---- alerts and cost

variable "alert_emails" {
  description = "Alert / budget email recipients."
  type        = list(string)
  default     = []
}

variable "monthly_budget_usd" {
  description = "Monthly budget for this environment."
  type        = number
  default     = 500
}

variable "cost_per_episode_threshold_usd" {
  description = "Alarm threshold for p90 model cost per episode."
  type        = number
  default     = 0.10
}
