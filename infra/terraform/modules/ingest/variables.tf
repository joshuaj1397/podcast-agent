variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK for the upload bucket and the queues."
  type        = string
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete a non-empty upload bucket (dev only)."
  type        = bool
  default     = false
}

variable "accepted_extensions" {
  description = "Transcript file extensions that trigger a job."
  type        = list(string)
  default     = ["json", "txt"]
}

variable "upload_retention_days" {
  description = "Days to keep raw transcripts under incoming/."
  type        = number
  default     = 30
}

variable "visibility_timeout_seconds" {
  description = "Jobs queue visibility timeout. Must exceed the maximum job duration (worker heartbeats extend it)."
  type        = number
  default     = 900

  validation {
    condition     = var.visibility_timeout_seconds >= 300 && var.visibility_timeout_seconds <= 43200
    error_message = "visibility_timeout_seconds must be between 300 and 43200 (SQS maximum)."
  }
}

variable "max_receive_count" {
  description = "Deliveries before a job message moves to the DLQ (poison-message protection)."
  type        = number
  default     = 3
}
