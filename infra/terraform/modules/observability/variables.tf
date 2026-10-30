variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "project" {
  description = "Value of the project cost allocation tag (budget filter)."
  type        = string
}

variable "environment" {
  description = "Environment name; the Environment dimension of the custom PodcastAgent metrics."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK for the alerts topic."
  type        = string
}

variable "alert_emails" {
  description = "Email addresses subscribed to alerts and budget notifications."
  type        = list(string)
  default     = []
}

# ---- resources to watch

variable "jobs_queue_name" {
  description = "Jobs queue name."
  type        = string
}

variable "jobs_dlq_name" {
  description = "Jobs DLQ name."
  type        = string
}

variable "review_queue_name" {
  description = "Human review queue name."
  type        = string
}

variable "max_receive_count" {
  description = "Deliveries before dead-lettering (for the alarm text)."
  type        = number
  default     = 3
}

variable "cluster_name" {
  description = "ECS cluster name."
  type        = string
}

variable "cluster_arn" {
  description = "ECS cluster ARN."
  type        = string
}

variable "service_name" {
  description = "ECS worker service name."
  type        = string
}

variable "log_group_name" {
  description = "Worker log group (agent trace events)."
  type        = string
}

variable "bedrock_model_id" {
  description = "Bedrock model id of the base model."
  type        = string
}

variable "enable_sagemaker_endpoint" {
  description = "Whether the LoRA endpoint exists."
  type        = bool
  default     = false
}

variable "sagemaker_endpoint_name" {
  description = "LoRA endpoint name (null when disabled)."
  type        = string
  default     = null
}

variable "sagemaker_variant_name" {
  description = "LoRA endpoint variant name."
  type        = string
  default     = "lora"
}

# ---- thresholds

variable "oldest_message_age_threshold_seconds" {
  description = "Alarm when the oldest waiting job is older than this (latency objective)."
  type        = number
  default     = 1800
}

variable "error_rate_threshold" {
  description = "Alarm when the failed-job ratio exceeds this over 15 minutes."
  type        = number
  default     = 0.1
}

variable "degraded_rate_threshold" {
  description = "Alarm when the share of degraded (deterministic-fallback) episodes exceeds this over an hour."
  type        = number
  default     = 0.2
}

variable "cost_per_episode_threshold_usd" {
  description = "Alarm when p90 estimated model cost per episode exceeds this."
  type        = number
  default     = 0.10
}

variable "bedrock_throttle_threshold" {
  description = "Throttled Bedrock calls per 5 minutes before alarming (the worker retries with backoff)."
  type        = number
  default     = 20
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget for resources tagged project=<project>."
  type        = number
  default     = 500
}
