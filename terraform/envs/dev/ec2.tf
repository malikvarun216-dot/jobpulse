# ── Data source: latest Amazon Linux 2023 AMI ────────────────────────────────

data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ── Security group ────────────────────────────────────────────────────────────

resource "aws_security_group" "dashboard" {
  name        = "${var.project}-dashboard-sg-${var.env}"
  description = "Streamlit dashboard: allow port 8501 and SSH"

  ingress {
    description = "Streamlit"
    from_port   = 8501
    to_port     = 8501
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH for GitHub Actions deploy"
    from_port   = 22
    to_port     = 22
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
    Name = "${var.project}-dashboard-sg-${var.env}"
  }
}

# ── IAM role for EC2 → Athena / S3 / Glue / Secrets Manager ──────────────────

data "aws_iam_policy_document" "ec2_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dashboard" {
  name               = "${var.project}-dashboard-role-${var.env}"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

data "aws_iam_policy_document" "dashboard_permissions" {
  statement {
    sid    = "AthenaQuery"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
    ]
    resources = [
      aws_athena_workgroup.main.arn,
    ]
  }

  statement {
    sid    = "S3GoldRead"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.layers["gold"].arn,
      "${aws_s3_bucket.layers["gold"].arn}/*",
      aws_s3_bucket.layers["silver"].arn,
      "${aws_s3_bucket.layers["silver"].arn}/*",
    ]
  }

  statement {
    sid    = "AthenaResultsWrite"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:AbortMultipartUpload",
    ]
    resources = [
      "${aws_s3_bucket.layers["gold"].arn}/athena-results/*",
    ]
  }

  statement {
    sid    = "GlueCatalogRead"
    effect = "Allow"
    actions = [
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetPartition",
      "glue:GetPartitions",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "SecretsManagerRead"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:*:secret:jobpulse/*",
    ]
  }
}

resource "aws_iam_policy" "dashboard" {
  name   = "${var.project}-dashboard-policy-${var.env}"
  policy = data.aws_iam_policy_document.dashboard_permissions.json
}

resource "aws_iam_role_policy_attachment" "dashboard" {
  role       = aws_iam_role.dashboard.name
  policy_arn = aws_iam_policy.dashboard.arn
}

resource "aws_iam_instance_profile" "dashboard" {
  name = "${var.project}-dashboard-profile-${var.env}"
  role = aws_iam_role.dashboard.name
}

# ── EC2 instance ──────────────────────────────────────────────────────────────

resource "aws_instance" "dashboard" {
  ami                    = data.aws_ami.amazon_linux_2023.id
  instance_type          = "t3.micro"
  key_name               = "${var.project}-${var.env}"
  vpc_security_group_ids = [aws_security_group.dashboard.id]
  iam_instance_profile   = aws_iam_instance_profile.dashboard.name

  user_data = <<-EOF
    #!/bin/bash
    yum update -y
    yum install -y docker
    systemctl start docker
    systemctl enable docker
    usermod -aG docker ec2-user

    # Directories for code deployment; code deployed via GitHub Actions
    mkdir -p /home/ec2-user/jobpulse/dashboard/streamlit
    chown -R ec2-user:ec2-user /home/ec2-user/jobpulse
  EOF

  tags = {
    Name = "${var.project}-dashboard-${var.env}"
  }
}

# ── Elastic IP ────────────────────────────────────────────────────────────────

resource "aws_eip" "dashboard" {
  instance = aws_instance.dashboard.id
  domain   = "vpc"

  tags = {
    Name = "${var.project}-dashboard-eip-${var.env}"
  }
}
