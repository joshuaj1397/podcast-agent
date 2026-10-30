variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK for artifacts, checkpoints and training volumes."
  type        = string
}

variable "harness_bucket_arn" {
  description = "Harness registry bucket ARN (read released versions, write candidates/)."
  type        = string
}

variable "bedrock_model_arn" {
  description = "Bedrock foundation-model ARN of the base model (Evo² search runs the agent)."
  type        = string
}

variable "training_image_repository_arns" {
  description = "ECR repositories of training images (e.g. the AWS deep learning container repos for PyTorch/HF training in this region)."
  type        = list(string)
  default     = ["arn:aws:ecr:*:763104351884:repository/*"]
}

variable "checkpoint_retention_days" {
  description = "Days to keep spot-training checkpoints."
  type        = number
  default     = 14
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete a non-empty artifacts bucket (dev only)."
  type        = bool
  default     = false
}
