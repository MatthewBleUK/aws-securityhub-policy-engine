resource "aws_cloudwatch_event_rule" "findings" {
  name = "${var.project_name}-findings"
  event_pattern = jsonencode({
    source        = ["aws.securityhub"]
    "detail-type" = ["Security Hub Findings - Imported"]
  })
}

resource "aws_cloudwatch_event_target" "findings" {
  rule       = aws_cloudwatch_event_rule.findings.name
  target_id  = "FindingsQueue"
  arn        = aws_sqs_queue.findings.arn
  depends_on = [aws_sqs_queue_policy.findings]
}

resource "aws_scheduler_schedule_group" "reconcile" {
  name = "${var.project_name}-reconcile"
}

resource "aws_scheduler_schedule" "reconcile" {
  name                = "${var.project_name}-daily-reconcile"
  group_name          = aws_scheduler_schedule_group.reconcile.name
  schedule_expression = "rate(1 day)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.processor.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ action = "reconcile" })
  }

  depends_on = [aws_iam_role_policy.scheduler]
}
