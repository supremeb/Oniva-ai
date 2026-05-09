# Oniva AWS Infrastructure
# Usage: terraform init && terraform apply

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Variables
variable "aws_region" {
  description = "AWS region"
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  default     = "production"
}

variable "db_password" {
  description = "PostgreSQL password"
  sensitive   = true
}

variable "secret_key" {
  description = "Application secret key"
  sensitive   = true
}

# VPC
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "oniva-${var.environment}"
  cidr = "10.0.0.0/16"

  azs             = ["${var.aws_region}a", "${var.aws_region}b"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24"]

  enable_nat_gateway = true
  single_nat_gateway = true

  tags = {
    Environment = var.environment
    Application = "oniva"
  }
}

# RDS PostgreSQL
resource "aws_db_subnet_group" "main" {
  name       = "oniva-${var.environment}"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "rds" {
  name_prefix = "oniva-rds-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = {
    Name = "oniva-rds-${var.environment}"
  }
}

resource "aws_db_instance" "main" {
  identifier     = "oniva-${var.environment}"
  engine         = "postgres"
  engine_version = "16"
  instance_class = "db.t3.medium"

  allocated_storage     = 100
  max_allocated_storage = 500
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "oniva"
  username = "postgres"
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "Mon:04:00-Mon:05:00"

  skip_final_snapshot       = false
  final_snapshot_identifier = "oniva-${var.environment}-final"

  tags = {
    Environment = var.environment
    Application = "oniva"
  }
}

# ElastiCache Redis
resource "aws_elasticache_subnet_group" "main" {
  name       = "oniva-${var.environment}"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "redis" {
  name_prefix = "oniva-redis-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = {
    Name = "oniva-redis-${var.environment}"
  }
}

resource "aws_elasticache_cluster" "main" {
  cluster_id           = "oniva-${var.environment}"
  engine               = "redis"
  engine_version       = "7.0"
  node_type            = "cache.t3.medium"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.redis.id]

  tags = {
    Environment = var.environment
    Application = "oniva"
  }
}

# ECS Fargate Cluster
resource "aws_ecs_cluster" "main" {
  name = "oniva-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Environment = var.environment
    Application = "oniva"
  }
}

resource "aws_security_group" "app" {
  name_prefix = "oniva-app-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  ingress {
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "oniva-app-${var.environment}"
  }
}

# ALB for public access
resource "aws_security_group" "alb" {
  name_prefix = "oniva-alb-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "oniva-alb-${var.environment}"
  }
}

resource "aws_lb" "main" {
  name               = "oniva-${var.environment}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = module.vpc.public_subnets

  tags = {
    Environment = var.environment
    Application = "oniva"
  }
}

# ECR Repositories
resource "aws_ecr_repository" "api" {
  name                 = "oniva-api"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Environment = var.environment
    Application = "oniva"
  }
}

resource "aws_ecr_repository" "frontend" {
  name                 = "oniva-frontend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Environment = var.environment
    Application = "oniva"
  }
}

# Secrets Manager for sensitive values
resource "aws_secretsmanager_secret" "app_secrets" {
  name = "oniva-${var.environment}-secrets"

  tags = {
    Environment = var.environment
    Application = "oniva"
  }
}

resource "aws_secretsmanager_secret_version" "app_secrets" {
  secret_id = aws_secretsmanager_secret.app_secrets.id
  secret_string = jsonencode({
    SECRET_KEY  = var.secret_key
    DB_PASSWORD = var.db_password
  })
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/oniva-${var.environment}"
  retention_in_days = 30

  tags = {
    Environment = var.environment
    Application = "oniva"
  }
}

# Outputs
output "rds_endpoint" {
  value       = aws_db_instance.main.endpoint
  description = "RDS PostgreSQL endpoint"
}

output "redis_endpoint" {
  value       = aws_elasticache_cluster.main.cache_nodes[0].address
  description = "ElastiCache Redis endpoint"
}

output "vpc_id" {
  value       = module.vpc.vpc_id
  description = "VPC ID"
}

output "ecs_cluster_name" {
  value       = aws_ecs_cluster.main.name
  description = "ECS Cluster name"
}

output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "ALB DNS name"
}

output "ecr_api_url" {
  value       = aws_ecr_repository.api.repository_url
  description = "ECR API repository URL"
}

output "ecr_frontend_url" {
  value       = aws_ecr_repository.frontend.repository_url
  description = "ECR Frontend repository URL"
}
