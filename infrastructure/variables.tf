variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "project_name" {
  type    = string
  default = "securityhub-policy-engine"
}

variable "manage_securityhub" {
  type    = bool
  default = true
}

variable "enable_default_standards" {
  type    = bool
  default = true
}

variable "github_repository" {
  type        = string
  description = "GitHub owner/repository, used for the policy-publishing OIDC role"
}

variable "github_owner_id" {
  type        = string
  default     = ""
  description = "Numeric GitHub owner ID, required for repositories using immutable OIDC subjects"
}

variable "github_repo_id" {
  type        = string
  default     = ""
  description = "Numeric GitHub repository ID, required for repositories using immutable OIDC subjects"
}

variable "expiry_notification_email" {
  type        = string
  default     = ""
  description = "Email address for expiry reminders. Leave empty to disable notifications. The recipient must confirm the SNS subscription."
}

variable "expiry_warning_days" {
  type        = number
  default     = 30
  description = "Send one reminder when a policy enters this many days before expiry."
  validation {
    condition     = var.expiry_warning_days >= 1 && floor(var.expiry_warning_days) == var.expiry_warning_days
    error_message = "The reminder window must be a positive whole number of days."
  }
}
