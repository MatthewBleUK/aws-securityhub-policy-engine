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
