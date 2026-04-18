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
}

