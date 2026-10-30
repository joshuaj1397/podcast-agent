output "endpoint_name" {
  description = "LoRA endpoint name (null when disabled)."
  value       = one(aws_sagemaker_endpoint.lora[*].name)
}

output "endpoint_arn" {
  description = "LoRA endpoint ARN (null when disabled)."
  value       = one(aws_sagemaker_endpoint.lora[*].arn)
}

output "variant_name" {
  description = "Production variant name (CloudWatch dimension)."
  value       = local.variant_name
}

output "role_arn" {
  description = "Endpoint execution role ARN (null when disabled)."
  value       = one(aws_iam_role.endpoint[*].arn)
}
