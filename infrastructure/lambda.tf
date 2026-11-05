data "archive_file" "processor" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/processor.zip"
}

resource "aws_cloudwatch_log_group" "processor" {
  name              = "/aws/lambda/${var.project_name}-processor"
  retention_in_days = 90
}

resource "aws_lambda_function" "processor" {
  function_name    = "${var.project_name}-processor"
  runtime          = "python3.12"
  handler          = "engine.handler.lambda_handler"
  role             = aws_iam_role.lambda.arn
  filename         = data.archive_file.processor.output_path
  source_code_hash = data.archive_file.processor.output_base64sha256
  memory_size      = 256
  timeout          = 60

  environment {
    variables = {
      POLICIES_TABLE      = aws_dynamodb_table.policies.name
      QUEUE_URL           = aws_sqs_queue.findings.url
      EXPIRY_TOPIC_ARN    = var.expiry_notification_email != "" ? aws_sns_topic.expiry[0].arn : ""
      EXPIRY_WARNING_DAYS = tostring(var.expiry_warning_days)
    }
  }

  depends_on = [aws_cloudwatch_log_group.processor, aws_iam_role_policy.lambda]
}

resource "aws_lambda_event_source_mapping" "findings" {
  event_source_arn        = aws_sqs_queue.findings.arn
  function_name           = aws_lambda_function.processor.arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
}
