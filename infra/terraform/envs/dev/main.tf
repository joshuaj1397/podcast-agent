# Podcast agent, dev environment. Never applied as part of the take-home; validated only.
# stage/prod are copies of this directory with their own tfvars and state (see docs/deployment.md).

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  name_prefix = "${var.project}-${var.environment}"

  # NOTE: verify the model id against the Bedrock model catalog in the target region before applying
  # (`aws bedrock list-foundation-models --by-provider google`). Some models are only reachable through a
  # cross-region inference profile (e.g. "us.<model-id>"); then grant the profile ARN as well.
  bedrock_model_arn = "arn:${data.aws_partition.current.partition}:bedrock:${data.aws_region.current.region}::foundation-model/${var.bedrock_model_id}"
}

module "network" {
  source = "../../modules/network"

  name_prefix        = local.name_prefix
  vpc_cidr           = var.vpc_cidr
  availability_zones = data.aws_availability_zones.available.names
  az_count           = var.az_count
  enable_nat_gateway = var.enable_nat_gateway
}

module "data" {
  source = "../../modules/data"

  name_prefix         = local.name_prefix
  force_destroy       = var.force_destroy
  deletion_protection = var.environment == "prod"
  initial_model       = "bedrock/${var.bedrock_model_id}"

  enable_opensearch           = var.enable_opensearch
  opensearch_standby_replicas = var.environment == "prod" ? "ENABLED" : "DISABLED"
  opensearch_reader_arns      = [module.compute.task_role_arn]
  opensearch_writer_arns      = var.kb_writer_arns
  vpc_id                      = module.network.vpc_id
  subnet_ids                  = module.network.private_subnet_ids
  endpoint_security_group_id  = module.network.endpoints_security_group_id
}

module "ingest" {
  source = "../../modules/ingest"

  name_prefix                = local.name_prefix
  kms_key_arn                = module.data.kms_key_arn
  force_destroy              = var.force_destroy
  visibility_timeout_seconds = var.job_visibility_timeout_seconds
  max_receive_count          = var.max_receive_count
}

module "training" {
  source = "../../modules/training"

  name_prefix        = local.name_prefix
  kms_key_arn        = module.data.kms_key_arn
  harness_bucket_arn = module.data.harness_bucket_arn
  bedrock_model_arn  = local.bedrock_model_arn
  force_destroy      = var.force_destroy
}

module "model" {
  source = "../../modules/model"

  enabled                          = var.enable_lora_endpoint
  name_prefix                      = local.name_prefix
  image_uri                        = var.lora_image_uri
  model_data_s3_uri                = var.lora_model_data_s3_uri
  model_version                    = var.lora_model_version
  instance_type                    = var.lora_instance_type
  data_capture_sampling_percentage = var.lora_data_capture_percentage
  subnet_ids                       = module.network.private_subnet_ids
  security_group_id                = module.network.sagemaker_security_group_id
  kms_key_arn                      = module.data.kms_key_arn
  artifacts_bucket_name            = module.training.artifacts_bucket_name
  artifacts_bucket_arn             = module.training.artifacts_bucket_arn
}

module "compute" {
  source = "../../modules/compute"

  name_prefix       = local.name_prefix
  environment       = var.environment
  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.network.workers_security_group_id
  kms_key_arn       = module.data.kms_key_arn

  upload_bucket_arn      = module.ingest.upload_bucket_arn
  results_bucket_name    = module.data.results_bucket_name
  results_bucket_arn     = module.data.results_bucket_arn
  harness_bucket_name    = module.data.harness_bucket_name
  harness_bucket_arn     = module.data.harness_bucket_arn
  jobs_table_name        = module.data.jobs_table_name
  jobs_table_arn         = module.data.jobs_table_arn
  jobs_queue_url         = module.ingest.jobs_queue_url
  jobs_queue_arn         = module.ingest.jobs_queue_arn
  jobs_queue_name        = module.ingest.jobs_queue_name
  review_queue_url       = module.ingest.review_queue_url
  review_queue_arn       = module.ingest.review_queue_arn
  release_parameter_name = module.data.release_parameter_name
  release_parameter_arn  = module.data.release_parameter_arn

  bedrock_model_id               = var.bedrock_model_id
  enable_sagemaker_endpoint      = var.enable_lora_endpoint
  sagemaker_endpoint_name        = module.model.endpoint_name
  enable_opensearch              = var.enable_opensearch
  opensearch_collection_arn      = module.data.opensearch_collection_arn
  opensearch_collection_endpoint = module.data.opensearch_collection_endpoint

  image_tag               = var.image_tag
  task_cpu                = var.worker_cpu
  task_memory             = var.worker_memory
  worker_concurrency      = var.worker_concurrency
  min_workers             = var.min_workers
  max_workers             = var.max_workers
  target_backlog_per_task = var.target_backlog_per_task
}

module "observability" {
  source = "../../modules/observability"

  name_prefix       = local.name_prefix
  project           = var.project
  environment       = var.environment
  kms_key_arn       = module.data.kms_key_arn
  alert_emails      = var.alert_emails
  jobs_queue_name   = module.ingest.jobs_queue_name
  jobs_dlq_name     = module.ingest.jobs_dlq_name
  review_queue_name = module.ingest.review_queue_name
  max_receive_count = var.max_receive_count
  cluster_name      = module.compute.cluster_name
  cluster_arn       = module.compute.cluster_arn
  service_name      = module.compute.service_name
  log_group_name    = module.compute.log_group_name
  bedrock_model_id  = var.bedrock_model_id

  enable_sagemaker_endpoint = var.enable_lora_endpoint
  sagemaker_endpoint_name   = module.model.endpoint_name
  sagemaker_variant_name    = module.model.variant_name

  monthly_budget_usd             = var.monthly_budget_usd
  cost_per_episode_threshold_usd = var.cost_per_episode_threshold_usd
}
