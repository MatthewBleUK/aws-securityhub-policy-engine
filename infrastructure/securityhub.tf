resource "aws_securityhub_account" "securityhub" {
  count                    = var.manage_securityhub ? 1 : 0
  enable_default_standards = var.enable_default_standards

  lifecycle {
    prevent_destroy = true
  }
}
