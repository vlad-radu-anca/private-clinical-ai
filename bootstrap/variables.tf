variable "region" {
  description = "Region for the provider. IAM is global, so this only decides where the API calls go."
  type        = string
  default     = "us-west-2"
}

variable "github_repository" {
  description = "The repository allowed to assume the roles, as owner/name."
  type        = string
  default     = "vlad-radu-anca/private-clinical-ai"
}

variable "deploy_environment" {
  description = "GitHub environment the deploy role is tied to. Protect it with a required reviewer, so every deploy needs an approval."
  type        = string
  default     = "aws-demo"
}

variable "state_bucket" {
  description = "S3 bucket holding the Terraform state (the bucket in terraform/backend.hcl)."
  type        = string
}

variable "state_prefix" {
  description = "Key prefix of this project's state objects in the bucket."
  type        = string
  default     = "private-clinical-ai/"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC identity provider. An account can hold only one per URL: set false if another project already created it."
  type        = bool
  default     = true
}
