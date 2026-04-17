terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state for now — we'll move to S3 backend after first apply
  backend "local" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project = var.project
      env     = var.env
      owner   = var.owner
    }
  }
}