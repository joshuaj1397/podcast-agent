output "ecr_repository_url" {
  description = "ECR repository for the agent image (CI pushes <url>:<git sha>)."
  value       = aws_ecr_repository.agent.repository_url
}

output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  description = "ECS cluster ARN."
  value       = aws_ecs_cluster.this.arn
}

output "service_name" {
  description = "ECS worker service name."
  value       = aws_ecs_service.worker.name
}

output "task_definition_arn" {
  description = "Current worker task definition revision."
  value       = aws_ecs_task_definition.worker.arn
}

output "task_role_arn" {
  description = "Worker task role ARN (granted read access to the KB collection)."
  value       = aws_iam_role.task.arn
}

output "execution_role_arn" {
  description = "Worker execution role ARN."
  value       = aws_iam_role.execution.arn
}

output "log_group_name" {
  description = "Worker log group (agent traces as JSON, EMF metrics)."
  value       = aws_cloudwatch_log_group.worker.name
}

output "search_api_secret_arn" {
  description = "Secret for the external web-search API key (set out of band)."
  value       = aws_secretsmanager_secret.search_api.arn
}
