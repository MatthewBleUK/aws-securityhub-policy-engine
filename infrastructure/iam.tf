data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.project_name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.processor.arn}:*"]
  }
  statement {
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:SendMessage"]
    resources = [aws_sqs_queue.findings.arn]
  }
  statement {
    actions   = ["dynamodb:Query"]
    resources = [aws_dynamodb_table.policies.arn]
  }
  statement {
    actions   = ["securityhub:GetFindings", "securityhub:BatchUpdateFindings"]
    resources = ["*"]
  }
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:${data.aws_partition.current.partition}:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-processor"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${var.project_name}-lambda"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

locals {
  github_provider_arn = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
  github_parts        = split("/", var.github_repository)
  github_subject      = var.github_owner_id != "" && var.github_repo_id != "" ? "repo:${local.github_parts[0]}@${var.github_owner_id}/${local.github_parts[1]}@${var.github_repo_id}:ref:refs/heads/main" : "repo:${var.github_repository}:ref:refs/heads/main"
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_subject]
    }
  }
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
    # Scheduler supplies the schedule group ARN, not the individual schedule ARN.
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_scheduler_schedule_group.reconcile.arn]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.project_name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.processor.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${var.project_name}-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_iam_role" "github_policy_deploy" {
  name               = "${var.project_name}-policy-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

data "aws_iam_policy_document" "github_policy_deploy" {
  statement {
    actions   = ["dynamodb:Scan", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.policies.arn]
  }
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.processor.arn]
  }
}

resource "aws_iam_role_policy" "github_policy_deploy" {
  role   = aws_iam_role.github_policy_deploy.id
  policy = data.aws_iam_policy_document.github_policy_deploy.json
}

data "aws_iam_policy_document" "expiry" {
  count = var.expiry_notification_email != "" ? 1 : 0
  statement {
    actions   = ["dynamodb:Scan", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.policies.arn]
  }
  statement {
    actions   = ["sns:Publish", "sns:ListSubscriptionsByTopic"]
    resources = [aws_sns_topic.expiry[0].arn]
  }
}

resource "aws_iam_role_policy" "expiry" {
  count  = var.expiry_notification_email != "" ? 1 : 0
  name   = "${var.project_name}-expiry-notifications"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.expiry[0].json
}
