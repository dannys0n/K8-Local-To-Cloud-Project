data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 3)

  tags = merge({
    Project     = var.cluster_name
    Environment = "lab"
    ManagedBy   = "terraform"
  }, var.tags)

  repositories = {
    server     = "simple-tcp-server"
    gateway    = "tcp-gateway"
    autoscaler = "tcp-server-autoscaler"
  }
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.6.1"

  name = var.cluster_name
  cidr = var.vpc_cidr
  azs  = local.azs

  private_subnets = [for index, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, index)]
  public_subnets  = [for index, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, index + 8)]

  enable_nat_gateway   = true
  single_nat_gateway   = var.single_nat_gateway
  enable_dns_hostnames = true
  enable_dns_support   = true

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.24.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  endpoint_public_access                   = true
  endpoint_public_access_cidrs             = var.nlb_source_cidrs
  enable_cluster_creator_admin_permissions = true

  compute_config = {
    enabled    = true
    node_pools = ["general-purpose"]
  }

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets
}

resource "aws_ecr_repository" "workload" {
  for_each = local.repositories

  name                 = each.value
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "workload" {
  for_each = aws_ecr_repository.workload

  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain the newest 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_security_group" "valkey" {
  name_prefix = "${var.cluster_name}-valkey-"
  description = "Valkey access from EKS workloads"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "TLS Valkey from the EKS cluster security group"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.cluster_primary_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_elasticache_serverless_cache" "valkey" {
  engine               = "valkey"
  name                 = replace(var.cluster_name, "_", "-")
  description          = "Authoritative state for the ${var.cluster_name} EKS lab"
  major_engine_version = "7"

  subnet_ids         = module.vpc.private_subnets
  security_group_ids = [aws_security_group.valkey.id]

  snapshot_retention_limit = 1

  cache_usage_limits {
    data_storage {
      maximum = var.valkey_max_data_gb
      unit    = "GB"
    }
    ecpu_per_second {
      maximum = var.valkey_max_ecpu_per_second
    }
  }
}
