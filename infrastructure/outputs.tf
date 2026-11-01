output "policies_table_name" {
  value = aws_dynamodb_table.policies.name
}

output "processor_function_name" {
  value = aws_lambda_function.processor.function_name
}

output "github_policy_role_arn" {
  value = aws_iam_role.github_policy_deploy.arn
}

output "finding_queue_url" {
  value = aws_sqs_queue.findings.url
}

output "dead_letter_queue_url" {
  value = aws_sqs_queue.dlq.url
}
