variable "enabled" {
  description = "Create the LoRA task-model endpoint. False = Bedrock-only base path (no GPU cost)."
  type        = bool
  default     = false
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
}

variable "image_uri" {
  description = "Serving image, e.g. an AWS LMI (DJL) container with the vLLM backend, or a vLLM image in our ECR. Required when enabled."
  type        = string
  default     = null
}

variable "model_data_s3_uri" {
  description = "S3 prefix (ending in /) with the base weights and adapters/<name>/ LoRA folders, under the artifacts bucket's models/ prefix. Required when enabled."
  type        = string
  default     = null
}

variable "model_version" {
  description = "Label of the model package being served (e.g. the Model Registry version). Changing it rolls the endpoint."
  type        = string
  default     = "r1"
}

variable "container_environment" {
  description = "Serving container environment. Defaults are LMI/vLLM multi-LoRA options; verify names against the pinned container version."
  type        = map(string)
  default = {
    OPTION_ROLLING_BATCH          = "vllm"
    OPTION_ENABLE_LORA            = "true"
    OPTION_MAX_LORAS              = "4" # stable + canary task-model adapters, evolver adapter, spare
    OPTION_MAX_LORA_RANK          = "64"
    OPTION_MAX_MODEL_LEN          = "16384" # matches harness generation.num_ctx
    OPTION_GPU_MEMORY_UTILIZATION = "0.9"
  }
}

variable "instance_type" {
  description = "GPU instance type. ml.g6.xlarge = 1x NVIDIA L4 (24 GB), ample for a ~5B-parameter model plus adapters."
  type        = string
  default     = "ml.g6.xlarge"
}

variable "min_instances" {
  description = "Minimum instances (real-time variants cannot go below 1)."
  type        = number
  default     = 1
}

variable "max_instances" {
  description = "Maximum instances."
  type        = number
  default     = 2
}

variable "target_invocations_per_instance" {
  description = "Target invocations per instance per minute for autoscaling (load-test to calibrate)."
  type        = number
  default     = 120
}

variable "data_capture_sampling_percentage" {
  description = "Percent of requests/responses captured to S3 for quality monitoring and the next training round. 0 disables capture."
  type        = number
  default     = 0
}

variable "subnet_ids" {
  description = "Private subnets for the endpoint ENIs."
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group for the endpoint ENIs."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK that encrypts model artifacts and captured data."
  type        = string
}

variable "artifacts_bucket_name" {
  description = "ML artifacts bucket name (from the training module)."
  type        = string
}

variable "artifacts_bucket_arn" {
  description = "ML artifacts bucket ARN (from the training module)."
  type        = string
}
