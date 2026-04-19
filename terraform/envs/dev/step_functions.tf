resource "aws_iam_role" "sfn_exec" {
  name = "${var.project}-sfn-exec-${var.env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "states.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "sfn_policy" {
  name = "${var.project}-sfn-policy-${var.env}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeLambda"
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.remotive.arn
      },
      {
        Sid    = "StartGlueJobs"
        Effect = "Allow"
        Action = ["glue:StartJobRun", "glue:GetJobRun", "glue:BatchStopJobRun"]
        Resource = [
          aws_glue_job.bronze_to_silver.arn,
          aws_glue_job.dbt_runner.arn,
          aws_glue_job.enrichment_runner.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "sfn_attach" {
  role       = aws_iam_role.sfn_exec.name
  policy_arn = aws_iam_policy.sfn_policy.arn
}

resource "aws_sfn_state_machine" "ingest_pipeline" {
  name     = "${var.project}-ingest-pipeline-${var.env}"
  role_arn = aws_iam_role.sfn_exec.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "JobPulse daily ingestion pipeline"
    StartAt = "InvokeRemotive"
    States = {
      InvokeRemotive = {
        Type       = "Task"
        Resource   = aws_lambda_function.remotive.arn
        ResultPath = "$.remotive"
        Next       = "CheckRemotive"
      }
      CheckRemotive = {
        Type = "Choice"
        Choices = [{
          Variable     = "$.remotive.status"
          StringEquals = "OK"
          Next         = "RunGlueJob"
        }]
        Default = "PipelineFailure"
      }
      RunGlueJob = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Parameters = {
          JobName = aws_glue_job.bronze_to_silver.name
          Arguments = {
            "--snapshot_date.$" = "$.remotive.snapshot_date"
          }
        }
        ResultPath = "$.glue"
        Next       = "RunDbtGold"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "PipelineFailure"
          ResultPath  = "$.error"
        }]
      }
      RunDbtGold = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Parameters = {
          JobName = aws_glue_job.dbt_runner.name
        }
        ResultPath = "$.dbt"
        Next       = "RunEnrichment"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "PipelineFailure"
          ResultPath  = "$.error"
        }]
      }
      RunEnrichment = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Parameters = {
          JobName = aws_glue_job.enrichment_runner.name
          Arguments = {
            "--snapshot_date.$" = "$.remotive.snapshot_date"
          }
        }
        ResultPath = "$.enrichment"
        Next       = "PipelineComplete"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "PipelineFailure"
          ResultPath  = "$.error"
        }]
      }
      PipelineComplete = {
        Type = "Succeed"
      }
      PipelineFailure = {
        Type  = "Fail"
        Cause = "Pipeline stage failed"
      }
    }
  })
}
