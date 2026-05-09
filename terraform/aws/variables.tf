# Oniva Terraform Variables
# Copy to terraform.tfvars and fill in values

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (production, staging, development)"
  type        = string
  default     = "production"
}

variable "db_password" {
  description = "PostgreSQL database password (min 16 characters)"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 16
    error_message = "Database password must be at least 16 characters."
  }
}

variable "secret_key" {
  description = "Application secret key for JWT signing (min 32 characters)"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.secret_key) >= 32
    error_message = "Secret key must be at least 32 characters."
  }
}

# Optional LLM API keys
variable "openai_api_key" {
  description = "OpenAI API key (optional)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "Anthropic API key (optional)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "openrouter_api_key" {
  description = "OpenRouter API key (optional)"
  type        = string
  default     = ""
  sensitive   = true
}
