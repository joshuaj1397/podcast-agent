output "upload_bucket" {
  description = "Drop transcripts at s3://<this>/incoming/<client>/<show>/<episode>.json|.txt"
  value       = module.ingest.upload_bucket_name
}

output "results_bucket" {
  description = "Reports and traces: s3://<this>/results/<client>/<episode>/<job key>/"
  value       = module.data.results_bucket_name
}

output "harness_bucket" {
  description = "Harness registry bucket."
  value       = module.data.harness_bucket_name
}

output "release_parameter" {
  description = "SSM parameter with the stable/canary harness + model versions."
  value       = module.data.release_parameter_name
}

output "jobs_queue_url" {
  description = "Jobs queue URL."
  value       = module.ingest.jobs_queue_url
}

output "review_queue_url" {
  description = "Human review queue URL (episodes with ⚠ claims)."
  value       = module.ingest.review_queue_url
}

output "jobs_table" {
  description = "DynamoDB jobs table."
  value       = module.data.jobs_table_name
}

output "ecr_repository_url" {
  description = "Push the agent image here (tag = git SHA)."
  value       = module.compute.ecr_repository_url
}

output "ecs_cluster" {
  description = "ECS cluster name."
  value       = module.compute.cluster_name
}

output "ecs_service" {
  description = "ECS worker service name."
  value       = module.compute.service_name
}

output "worker_log_group" {
  description = "Worker log group (trace events, EMF metrics)."
  value       = module.compute.log_group_name
}

output "kb_collection_endpoint" {
  description = "OpenSearch Serverless KB endpoint (null if disabled)."
  value       = module.data.opensearch_collection_endpoint
}

output "lora_endpoint_name" {
  description = "SageMaker LoRA endpoint (null if disabled)."
  value       = module.model.endpoint_name
}

output "training_role_arn" {
  description = "Role for Evo² SageMaker training/processing jobs."
  value       = module.training.training_role_arn
}

output "checkpoint_s3_uri" {
  description = "Spot-training checkpoint prefix."
  value       = module.training.checkpoint_s3_uri
}

output "model_package_groups" {
  description = "SageMaker Model Registry groups."
  value       = module.training.model_package_group_names
}

output "alerts_topic_arn" {
  description = "SNS alerts topic."
  value       = module.observability.alerts_topic_arn
}

output "dashboard" {
  description = "CloudWatch dashboard name."
  value       = module.observability.dashboard_name
}
