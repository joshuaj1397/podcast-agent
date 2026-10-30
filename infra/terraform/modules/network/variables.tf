variable "name_prefix" {
  description = "Prefix for resource names, e.g. podcast-agent-dev."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC. Private subnets take /20s from the start, public (NAT) subnets /24s from the end."
  type        = string
  default     = "10.40.0.0/16"
}

variable "availability_zones" {
  description = "Candidate availability zones (usually data.aws_availability_zones.available.names)."
  type        = list(string)
}

variable "az_count" {
  description = "Number of AZs to spread private subnets (and interface endpoints) across."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 1 && var.az_count <= 3
    error_message = "az_count must be between 1 and 3."
  }
}

variable "enable_nat_gateway" {
  description = "Create public subnets, an internet gateway and one NAT gateway. Off by default: all AWS APIs go through VPC endpoints."
  type        = bool
  default     = false
}

variable "interface_endpoints" {
  description = "Interface VPC endpoint service names (without the com.amazonaws.<region>. prefix). Each costs ~USD 7/month per AZ."
  type        = list(string)
  default = [
    "ecr.api",           # image manifest pulls
    "ecr.dkr",           # image layer pulls (layers themselves come from S3 via the gateway endpoint)
    "logs",              # awslogs driver, EMF metrics
    "sqs",               # job and review queues
    "bedrock-runtime",   # base model (InvokeModel / Converse)
    "sagemaker.runtime", # LoRA task-model endpoint
    "secretsmanager",    # API keys (e.g. a real web-search provider)
    "ssm",               # release flags (stable/canary harness + model versions)
    "xray",              # OpenTelemetry traces via the ADOT sidecar
    # add "comprehend" when the workers use pii_redaction = "comprehend"
  ]
}
