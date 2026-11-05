resource "aws_sns_topic" "expiry" {
  count = var.expiry_notification_email != "" ? 1 : 0
  name  = "${var.project_name}-policy-expiry"
}

resource "aws_sns_topic_subscription" "expiry_email" {
  count     = var.expiry_notification_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.expiry[0].arn
  protocol  = "email"
  endpoint  = var.expiry_notification_email
}
