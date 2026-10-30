output "alerts_topic_arn" {
  description = "SNS topic receiving all alarms, task-failure events and budget notifications."
  value       = aws_sns_topic.alerts.arn
}

output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = aws_cloudwatch_dashboard.this.dashboard_name
}
