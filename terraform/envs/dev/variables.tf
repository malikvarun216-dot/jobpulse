variable "project" {
  default = "jobpulse"
}

variable "env" {
  default = "dev"
}

variable "aws_region" {
  default = "ap-south-1"
}

variable "owner" {
  default = "varun"
}

variable "alert_email" {
  description = "Email address for pipeline failure alerts via SNS"
  type        = string
  default     = "jobpulse010@gmail.com" # not a secret — the SNS subscription shows it anyway
}

# Secrets: never in a committed file. Set them in the shell before plan/apply:
#   export TF_VAR_adzuna_app_id=...   export TF_VAR_adzuna_app_key=...
# (or a local terraform.tfvars — gitignored). See docs/runbook.md → "Terraform secrets".
variable "adzuna_app_id" {
  description = "Adzuna API application ID (from developer.adzuna.com)"
  type        = string
  sensitive   = true
}

variable "adzuna_app_key" {
  description = "Adzuna API application key (from developer.adzuna.com)"
  type        = string
  sensitive   = true
}

