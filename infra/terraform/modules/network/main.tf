terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

# Private-only VPC for the workers, the model endpoint and training jobs.
# AWS APIs are reached through VPC endpoints, so there is no internet egress by default.
# Set enable_nat_gateway = true only if the agent must reach a real (non-AWS) web-search API.

data "aws_region" "current" {}

locals {
  azs    = slice(var.availability_zones, 0, var.az_count)
  region = data.aws_region.current.region
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # required for private DNS on interface endpoints

  tags = { Name = "${var.name_prefix}-vpc" }
}

# Lock down the default security group so nothing can use it by accident.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
}

resource "aws_subnet" "private" {
  count = length(local.azs)

  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)

  tags = { Name = "${var.name_prefix}-private-${local.azs[count.index]}", tier = "private" }
}

resource "aws_route_table" "private" {
  count  = length(local.azs)
  vpc_id = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-private-${local.azs[count.index]}" }
}

resource "aws_route_table_association" "private" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ---------------------------------------------------------------- optional NAT (off by default)

resource "aws_subnet" "public" {
  count = var.enable_nat_gateway ? length(local.azs) : 0

  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 240 + count.index)

  tags = { Name = "${var.name_prefix}-public-${local.azs[count.index]}", tier = "public" }
}

resource "aws_internet_gateway" "this" {
  count  = var.enable_nat_gateway ? 1 : 0
  vpc_id = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-igw" }
}

resource "aws_route_table" "public" {
  count  = var.enable_nat_gateway ? 1 : 0
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this[0].id
  }

  tags = { Name = "${var.name_prefix}-public" }
}

resource "aws_route_table_association" "public" {
  count          = var.enable_nat_gateway ? length(local.azs) : 0
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public[0].id
}

# A single NAT gateway keeps dev cheap; use one per AZ in prod.
resource "aws_eip" "nat" {
  count  = var.enable_nat_gateway ? 1 : 0
  domain = "vpc"

  tags = { Name = "${var.name_prefix}-nat" }
}

resource "aws_nat_gateway" "this" {
  count         = var.enable_nat_gateway ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id

  tags = { Name = "${var.name_prefix}-nat" }

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route" "private_nat" {
  count                  = var.enable_nat_gateway ? length(local.azs) : 0
  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[0].id
}

# ---------------------------------------------------------------- security groups

resource "aws_security_group" "workers" {
  name        = "${var.name_prefix}-workers"
  description = "ECS Fargate agent workers (no inbound; HTTPS out to VPC endpoints)"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-workers" }
}

resource "aws_security_group" "sagemaker" {
  name        = "${var.name_prefix}-sagemaker"
  description = "SageMaker endpoint and training job ENIs"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-sagemaker" }
}

resource "aws_security_group" "endpoints" {
  name        = "${var.name_prefix}-vpc-endpoints"
  description = "Interface VPC endpoints (HTTPS from inside the VPC only)"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${var.name_prefix}-vpc-endpoints" }
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_https" {
  security_group_id = aws_security_group.endpoints.id
  description       = "HTTPS from the VPC"
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# Distributed training (multi-instance) needs the instances to talk to each other.
resource "aws_vpc_security_group_ingress_rule" "sagemaker_self" {
  security_group_id            = aws_security_group.sagemaker.id
  description                  = "Inter-node traffic for multi-instance training"
  referenced_security_group_id = aws_security_group.sagemaker.id
  ip_protocol                  = "-1"
}

locals {
  egress_sgs = {
    workers   = aws_security_group.workers.id
    sagemaker = aws_security_group.sagemaker.id
  }
  # Without NAT, HTTPS can only go to the interface endpoints (VPC CIDR) and the S3/DynamoDB gateway endpoints.
  egress_prefix_lists = {
    s3       = aws_vpc_endpoint.s3.prefix_list_id
    dynamodb = aws_vpc_endpoint.dynamodb.prefix_list_id
  }
  egress_prefix_rules = {
    for pair in setproduct(keys(local.egress_sgs), keys(local.egress_prefix_lists)) :
    "${pair[0]}-${pair[1]}" => { sg = local.egress_sgs[pair[0]], prefix_list = local.egress_prefix_lists[pair[1]] }
  }
}

resource "aws_vpc_security_group_egress_rule" "https_vpc" {
  for_each = local.egress_sgs

  security_group_id = each.value
  description       = "HTTPS to interface VPC endpoints"
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "https_gateway_endpoints" {
  for_each = local.egress_prefix_rules

  security_group_id = each.value.sg
  description       = "HTTPS to S3/DynamoDB gateway endpoints"
  prefix_list_id    = each.value.prefix_list
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "https_internet" {
  for_each = var.enable_nat_gateway ? local.egress_sgs : {}

  security_group_id = each.value
  description       = "HTTPS to the internet through NAT (external search API)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "sagemaker_self" {
  security_group_id            = aws_security_group.sagemaker.id
  description                  = "Inter-node traffic for multi-instance training"
  referenced_security_group_id = aws_security_group.sagemaker.id
  ip_protocol                  = "-1"
}

# ---------------------------------------------------------------- VPC endpoints

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${local.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = { Name = "${var.name_prefix}-s3" }
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${local.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = { Name = "${var.name_prefix}-dynamodb" }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(var.interface_endpoints)

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${local.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]

  tags = { Name = "${var.name_prefix}-${each.value}" }
}
