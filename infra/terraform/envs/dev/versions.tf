terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Remote state (create the bucket once, out of band). Commented out so that
  # `terraform init -backend=false && terraform validate` works without AWS credentials.
  #
  # backend "s3" {
  #   bucket       = "acme-terraform-state-123456789012"
  #   key          = "podcast-agent/dev/terraform.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true # S3-native state locking (Terraform >= 1.10); use dynamodb_table on older versions
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project     = var.project
      env         = var.environment
      managed_by  = "terraform"
      cost_center = var.cost_center
    }
  }
}
