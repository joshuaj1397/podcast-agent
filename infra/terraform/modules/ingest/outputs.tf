output "upload_bucket_name" {
  description = "Upload bucket; drop transcripts under incoming/<client>/<show>/."
  value       = aws_s3_bucket.upload.bucket
}

output "upload_bucket_arn" {
  description = "Upload bucket ARN."
  value       = aws_s3_bucket.upload.arn
}

output "jobs_queue_url" {
  description = "Jobs queue URL."
  value       = aws_sqs_queue.jobs.id
}

output "jobs_queue_arn" {
  description = "Jobs queue ARN."
  value       = aws_sqs_queue.jobs.arn
}

output "jobs_queue_name" {
  description = "Jobs queue name (CloudWatch dimension)."
  value       = aws_sqs_queue.jobs.name
}

output "jobs_dlq_name" {
  description = "Jobs dead-letter queue name."
  value       = aws_sqs_queue.jobs_dlq.name
}

output "jobs_dlq_arn" {
  description = "Jobs dead-letter queue ARN."
  value       = aws_sqs_queue.jobs_dlq.arn
}

output "review_queue_url" {
  description = "Human review queue URL (episodes with ⚠ claims)."
  value       = aws_sqs_queue.review.id
}

output "review_queue_arn" {
  description = "Human review queue ARN."
  value       = aws_sqs_queue.review.arn
}

output "review_queue_name" {
  description = "Human review queue name."
  value       = aws_sqs_queue.review.name
}
