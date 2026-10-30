output "artifacts_bucket_name" {
  description = "ML artifacts bucket name."
  value       = aws_s3_bucket.artifacts.bucket
}

output "artifacts_bucket_arn" {
  description = "ML artifacts bucket ARN."
  value       = aws_s3_bucket.artifacts.arn
}

output "checkpoint_s3_uri" {
  description = "CheckpointConfig.S3Uri prefix for managed spot training jobs (append the job name)."
  value       = "s3://${aws_s3_bucket.artifacts.bucket}/checkpoints/"
}

output "training_role_arn" {
  description = "Execution role for SageMaker training / processing jobs."
  value       = aws_iam_role.training.arn
}

output "model_package_group_names" {
  description = "Model registry groups, keyed by task-model / evolver."
  value       = { for k, v in aws_sagemaker_model_package_group.this : k => v.model_package_group_name }
}
