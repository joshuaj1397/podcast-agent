output "kms_key_arn" {
  description = "ARN of the environment CMK."
  value       = aws_kms_key.this.arn
}

output "results_bucket_name" {
  description = "Results bucket (reports and traces)."
  value       = aws_s3_bucket.this["results"].bucket
}

output "results_bucket_arn" {
  description = "Results bucket ARN."
  value       = aws_s3_bucket.this["results"].arn
}

output "harness_bucket_name" {
  description = "Harness registry bucket (versioned)."
  value       = aws_s3_bucket.this["harness"].bucket
}

output "harness_bucket_arn" {
  description = "Harness registry bucket ARN."
  value       = aws_s3_bucket.this["harness"].arn
}

output "jobs_table_name" {
  description = "DynamoDB jobs table (status, idempotency keys, step checkpoints)."
  value       = aws_dynamodb_table.jobs.name
}

output "jobs_table_arn" {
  description = "DynamoDB jobs table ARN."
  value       = aws_dynamodb_table.jobs.arn
}

output "release_parameter_name" {
  description = "SSM parameter holding the stable/canary harness + model versions."
  value       = aws_ssm_parameter.release.name
}

output "release_parameter_arn" {
  description = "SSM release parameter ARN."
  value       = aws_ssm_parameter.release.arn
}

output "opensearch_collection_arn" {
  description = "KB collection ARN (null when OpenSearch is disabled)."
  value       = one(aws_opensearchserverless_collection.kb[*].arn)
}

output "opensearch_collection_endpoint" {
  description = "KB collection endpoint (null when OpenSearch is disabled)."
  value       = one(aws_opensearchserverless_collection.kb[*].collection_endpoint)
}
