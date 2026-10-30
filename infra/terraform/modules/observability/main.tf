terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

# Alarms -> SNS (email, or chat via AWS Chatbot), a dashboard, saved Logs Insights queries over the agent
# trace events, and an AWS Budgets budget. Custom metrics (namespace PodcastAgent, dimension Environment)
# are emitted by the worker as CloudWatch EMF / OTel metrics: JobsSucceeded, JobsFailed, JobsDegraded,
# EpisodeSeconds, EpisodeCostUsd, LlmCalls, ClaimsVerified, ClaimsInaccurate, ClaimsUnverifiable.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  ns         = "PodcastAgent"
  env_dim    = { Environment = var.environment }
}

# ---------------------------------------------------------------- SNS

resource "aws_sns_topic" "alerts" {
  name              = "${var.name_prefix}-alerts"
  kms_master_key_id = var.kms_key_arn
}

data "aws_iam_policy_document" "alerts" {
  statement {
    sid       = "AccountOwner"
    actions   = ["sns:Publish", "sns:Subscribe", "sns:GetTopicAttributes", "sns:SetTopicAttributes", "sns:ListSubscriptionsByTopic"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }
  statement {
    sid       = "AwsServicesPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com", "events.amazonaws.com", "budgets.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

resource "aws_sns_topic_subscription" "email" {
  for_each  = toset(var.alert_emails)
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

# ---------------------------------------------------------------- queue alarms

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${var.name_prefix}-jobs-dlq-not-empty"
  alarm_description   = "Episodes failed ${var.max_receive_count}x and were dead-lettered. Inspect the job in DynamoDB and the trace, fix, then redrive the DLQ."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.jobs_dlq_name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "oldest_job_age" {
  alarm_name          = "${var.name_prefix}-jobs-oldest-message-age"
  alarm_description   = "Jobs are waiting longer than the latency objective: workers are stuck, at max capacity, or throttled by the model."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = var.jobs_queue_name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.oldest_message_age_threshold_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "review_backlog" {
  alarm_name          = "${var.name_prefix}-review-backlog-age"
  alarm_description   = "Episodes with ⚠ claims have waited more than a day for human review."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = var.review_queue_name }
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 86400
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# ---------------------------------------------------------------- job quality / error alarms (custom metrics)

resource "aws_cloudwatch_metric_alarm" "job_error_rate" {
  alarm_name          = "${var.name_prefix}-job-error-rate"
  alarm_description   = "More than ${var.error_rate_threshold * 100}% of jobs failed over 15 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = var.error_rate_threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  metric_query {
    id = "failed"
    metric {
      namespace   = local.ns
      metric_name = "JobsFailed"
      dimensions  = local.env_dim
      period      = 300
      stat        = "Sum"
    }
  }
  metric_query {
    id = "succeeded"
    metric {
      namespace   = local.ns
      metric_name = "JobsSucceeded"
      dimensions  = local.env_dim
      period      = 300
      stat        = "Sum"
    }
  }
  metric_query {
    id          = "error_rate"
    label       = "Job error rate"
    expression  = "failed / (FILL(failed, 0) + FILL(succeeded, 0))"
    return_data = true
  }
}

# Degraded = a step fell back to the deterministic path (report.degraded). Not an outage, but a quality drop.
resource "aws_cloudwatch_metric_alarm" "degraded_rate" {
  alarm_name          = "${var.name_prefix}-degraded-rate"
  alarm_description   = "More than ${var.degraded_rate_threshold * 100}% of episodes used a deterministic fallback in the last hour (model errors, schema failures, or a bad harness)."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = var.degraded_rate_threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  metric_query {
    id = "degraded"
    metric {
      namespace   = local.ns
      metric_name = "JobsDegraded"
      dimensions  = local.env_dim
      period      = 3600
      stat        = "Sum"
    }
  }
  metric_query {
    id = "succeeded"
    metric {
      namespace   = local.ns
      metric_name = "JobsSucceeded"
      dimensions  = local.env_dim
      period      = 3600
      stat        = "Sum"
    }
  }
  metric_query {
    id          = "degraded_rate"
    label       = "Degraded episode rate"
    expression  = "degraded / succeeded"
    return_data = true
  }
}

resource "aws_cloudwatch_metric_alarm" "cost_per_episode" {
  alarm_name          = "${var.name_prefix}-cost-per-episode"
  alarm_description   = "p90 estimated model cost per episode above USD ${var.cost_per_episode_threshold_usd} (runaway repairs/tool loops, or a price change)."
  namespace           = local.ns
  metric_name         = "EpisodeCostUsd"
  dimensions          = local.env_dim
  extended_statistic  = "p90"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.cost_per_episode_threshold_usd
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# ---------------------------------------------------------------- model alarms

resource "aws_cloudwatch_metric_alarm" "bedrock_throttles" {
  alarm_name          = "${var.name_prefix}-bedrock-throttles"
  alarm_description   = "Bedrock is throttling the base model: lower max_workers or request a quota increase / provisioned throughput."
  namespace           = "AWS/Bedrock"
  metric_name         = "InvocationThrottles"
  dimensions          = { ModelId = var.bedrock_model_id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.bedrock_throttle_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "endpoint_5xx" {
  count               = var.enable_sagemaker_endpoint ? 1 : 0
  alarm_name          = "${var.name_prefix}-lora-endpoint-5xx"
  alarm_description   = "The LoRA endpoint returns 5xx; workers fall back to Bedrock, so quality (not availability) is affected."
  namespace           = "AWS/SageMaker"
  metric_name         = "Invocation5XXErrors"
  dimensions          = { EndpointName = var.sagemaker_endpoint_name, VariantName = var.sagemaker_variant_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

# ---------------------------------------------------------------- ECS task failures (event-driven)
# Crashes and failed starts (bad image, missing permissions); scale-in and Spot interruptions are expected.

resource "aws_cloudwatch_event_rule" "task_failed" {
  name        = "${var.name_prefix}-worker-task-failed"
  description = "Worker task crashed or failed to start"
  event_pattern = jsonencode({
    source      = ["aws.ecs"]
    detail-type = ["ECS Task State Change"]
    detail = {
      clusterArn = [var.cluster_arn]
      group      = ["service:${var.service_name}"]
      lastStatus = ["STOPPED"]
      stopCode   = ["EssentialContainerExited", "TaskFailedToStart"]
    }
  })
}

resource "aws_cloudwatch_event_target" "task_failed" {
  rule      = aws_cloudwatch_event_rule.task_failed.name
  target_id = "alerts"
  arn       = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths = {
      task   = "$.detail.taskArn"
      code   = "$.detail.stopCode"
      reason = "$.detail.stoppedReason"
    }
    input_template = "\"[${var.name_prefix}] worker task <task> stopped (<code>): <reason>\""
  }
}

# ---------------------------------------------------------------- budget

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Requires the "project" cost allocation tag to be activated in Billing.
  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:project$%s", var.project)]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_sns_topic_arns  = [aws_sns_topic.alerts.arn]
    subscriber_email_addresses = var.alert_emails
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_sns_topic_arns  = [aws_sns_topic.alerts.arn]
    subscriber_email_addresses = var.alert_emails
  }
}

# ---------------------------------------------------------------- Logs Insights queries over trace events
# The worker writes every trace.jsonl event to stdout as JSON, so these fields are auto-discovered.

resource "aws_cloudwatch_query_definition" "verdicts" {
  name            = "${var.name_prefix}/fact-check-verdicts-per-day"
  log_group_names = [var.log_group_name]
  query_string    = <<-EOT
    filter kind = "result" and agent = "factcheck"
    | stats count(*) as claims by result.status, bin(1d) as day
    | sort day desc
  EOT
}

resource "aws_cloudwatch_query_definition" "degraded" {
  name            = "${var.name_prefix}/degraded-episodes"
  log_group_names = [var.log_group_name]
  query_string    = <<-EOT
    fields @timestamp, episode, stats.seconds, stats.llm_calls, @message
    | filter kind = "report" and ispresent(degraded.0)
    | sort @timestamp desc
    | limit 100
  EOT
}

resource "aws_cloudwatch_query_definition" "episode_reasoning" {
  name            = "${var.name_prefix}/episode-reasoning-trace"
  log_group_names = [var.log_group_name]
  query_string    = <<-EOT
    fields t, kind, agent, text, detail
    | filter episode = "REPLACE_WITH_EPISODE_ID"
    | sort t asc
  EOT
}

resource "aws_cloudwatch_query_definition" "critic_repairs" {
  name            = "${var.name_prefix}/critic-repairs-by-agent"
  log_group_names = [var.log_group_name]
  query_string    = <<-EOT
    filter kind = "warn" and text like /critic:/
    | parse text "critic: * attempt" as agent_name
    | stats count(*) as repairs by agent_name
    | sort repairs desc
  EOT
}

# ---------------------------------------------------------------- dashboard

locals {
  env_metric = ["Environment", var.environment]

  widgets = concat([
    {
      type = "metric", x = 0, y = 0, width = 12, height = 6
      properties = {
        title = "Jobs queue", region = local.region, view = "timeSeries", stat = "Maximum", period = 60
        metrics = [
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.jobs_queue_name, { label = "waiting" }],
          ["AWS/SQS", "ApproximateNumberOfMessagesNotVisible", "QueueName", var.jobs_queue_name, { label = "in progress" }],
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.jobs_dlq_name, { label = "DLQ" }],
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.review_queue_name, { label = "awaiting review" }],
        ]
      }
    },
    {
      type = "metric", x = 12, y = 0, width = 12, height = 6
      properties = {
        title = "Workers and oldest job age", region = local.region, view = "timeSeries", stat = "Maximum", period = 60
        metrics = [
          ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", var.cluster_name, "ServiceName", var.service_name, { label = "running workers" }],
          ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", var.jobs_queue_name, { label = "oldest job (s)", yAxis = "right" }],
        ]
      }
    },
    {
      type = "metric", x = 0, y = 6, width = 8, height = 6
      properties = {
        title = "Jobs", region = local.region, view = "timeSeries", stat = "Sum", period = 300
        metrics = [
          concat([local.ns, "JobsSucceeded"], local.env_metric),
          concat([local.ns, "JobsFailed"], local.env_metric),
          concat([local.ns, "JobsDegraded"], local.env_metric),
        ]
      }
    },
    {
      type = "metric", x = 8, y = 6, width = 8, height = 6
      properties = {
        title = "Episode latency (s) and model cost (USD)", region = local.region, view = "timeSeries", period = 300
        metrics = [
          concat([local.ns, "EpisodeSeconds"], local.env_metric, [{ stat = "p50", label = "latency p50" }]),
          concat([local.ns, "EpisodeSeconds"], local.env_metric, [{ stat = "p90", label = "latency p90" }]),
          concat([local.ns, "EpisodeCostUsd"], local.env_metric, [{ stat = "Average", label = "cost / episode", yAxis = "right" }]),
        ]
      }
    },
    {
      type = "metric", x = 16, y = 6, width = 8, height = 6
      properties = {
        title = "Verdict mix (drift)", region = local.region, view = "timeSeries", stacked = true, stat = "Sum", period = 3600
        metrics = [
          concat([local.ns, "ClaimsVerified"], local.env_metric, [{ label = "✅ verified" }]),
          concat([local.ns, "ClaimsInaccurate"], local.env_metric, [{ label = "⚠ inaccurate/outdated" }]),
          concat([local.ns, "ClaimsUnverifiable"], local.env_metric, [{ label = "❓ unverifiable" }]),
        ]
      }
    },
    {
      type = "metric", x = 0, y = 12, width = 12, height = 6
      properties = {
        title = "Bedrock base model", region = local.region, view = "timeSeries", stat = "Sum", period = 300
        metrics = [
          ["AWS/Bedrock", "Invocations", "ModelId", var.bedrock_model_id],
          ["AWS/Bedrock", "InvocationThrottles", "ModelId", var.bedrock_model_id],
          ["AWS/Bedrock", "InvocationServerErrors", "ModelId", var.bedrock_model_id],
          ["AWS/Bedrock", "InvocationLatency", "ModelId", var.bedrock_model_id, { stat = "p90", yAxis = "right" }],
        ]
      }
    },
    {
      type = "log", x = 12, y = 12, width = 12, height = 6
      properties = {
        title = "Recent degraded episodes", region = local.region, view = "table"
        query = "SOURCE '${var.log_group_name}' | fields @timestamp, episode, @message | filter kind = \"report\" and ispresent(degraded.0) | sort @timestamp desc | limit 20"
      }
    },
    ],
    var.enable_sagemaker_endpoint ? [{
      type = "metric", x = 0, y = 18, width = 24, height = 6
      properties = {
        title = "LoRA endpoint", region = local.region, view = "timeSeries", period = 300
        metrics = [
          ["AWS/SageMaker", "Invocations", "EndpointName", var.sagemaker_endpoint_name, "VariantName", var.sagemaker_variant_name, { stat = "Sum" }],
          ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", var.sagemaker_endpoint_name, "VariantName", var.sagemaker_variant_name, { stat = "Sum" }],
          ["AWS/SageMaker", "ModelLatency", "EndpointName", var.sagemaker_endpoint_name, "VariantName", var.sagemaker_variant_name, { stat = "p90", yAxis = "right" }],
        ]
      }
  }] : [])
}

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "${var.name_prefix}-overview"
  dashboard_body = jsonencode({ widgets = local.widgets })
}
