variable "name_prefix" {
  description = "Prefix for resource names (lowercase; OpenSearch Serverless names are limited to 32 characters)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.name_prefix))
    error_message = "name_prefix must be 3-21 lowercase letters, digits or hyphens (OpenSearch Serverless policy names add up to 11 characters and are capped at 32)."
  }
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete non-empty buckets (dev only)."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Enable DynamoDB deletion protection."
  type        = bool
  default     = true
}

variable "trace_retention_days" {
  description = "Days to keep trace.jsonl objects (they contain transcript excerpts)."
  type        = number
  default     = 90
}

variable "initial_harness_version" {
  description = "Harness version written to the release parameter on creation (afterwards the promotion pipeline owns it)."
  type        = string
  default     = "base"
}

variable "initial_model" {
  description = "Model (LiteLLM model string) written to the release parameter on creation."
  type        = string
}

# ---- OpenSearch Serverless

variable "enable_opensearch" {
  description = "Create the OpenSearch Serverless KB collection. If false, workers use in-process BM25 over a KB snapshot in S3 (what the CLI does today)."
  type        = bool
  default     = true
}

variable "opensearch_standby_replicas" {
  description = "ENABLED for production redundancy, DISABLED halves the minimum OCUs (dev/test)."
  type        = string
  default     = "DISABLED"

  validation {
    condition     = contains(["ENABLED", "DISABLED"], var.opensearch_standby_replicas)
    error_message = "opensearch_standby_replicas must be ENABLED or DISABLED."
  }
}

variable "opensearch_reader_arns" {
  description = "IAM principal ARNs allowed to read the KB indexes (the worker task role)."
  type        = list(string)
}

variable "opensearch_writer_arns" {
  description = "IAM principal ARNs allowed to create/update KB indexes (e.g. the CI role that loads kb/*.jsonl)."
  type        = list(string)
  default     = []
}

variable "vpc_id" {
  description = "VPC for the OpenSearch Serverless VPC endpoint."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the OpenSearch Serverless VPC endpoint."
  type        = list(string)
}

variable "endpoint_security_group_id" {
  description = "Security group for the OpenSearch Serverless VPC endpoint (HTTPS from the VPC)."
  type        = string
}
