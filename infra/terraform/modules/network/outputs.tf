output "vpc_id" {
  description = "VPC id."
  value       = aws_vpc.this.id
}

output "private_subnet_ids" {
  description = "Private subnet ids (one per AZ)."
  value       = aws_subnet.private[*].id
}

output "private_route_table_ids" {
  description = "Private route table ids."
  value       = aws_route_table.private[*].id
}

output "workers_security_group_id" {
  description = "Security group for the ECS agent workers."
  value       = aws_security_group.workers.id
}

output "sagemaker_security_group_id" {
  description = "Security group for the SageMaker endpoint and training jobs."
  value       = aws_security_group.sagemaker.id
}

output "endpoints_security_group_id" {
  description = "Security group attached to interface VPC endpoints (also used for the OpenSearch Serverless endpoint)."
  value       = aws_security_group.endpoints.id
}

output "s3_gateway_endpoint_id" {
  description = "S3 gateway endpoint id (usable in bucket policies with aws:SourceVpce)."
  value       = aws_vpc_endpoint.s3.id
}

output "interface_endpoint_ids" {
  description = "Interface endpoint ids keyed by service."
  value       = { for k, v in aws_vpc_endpoint.interface : k => v.id }
}
