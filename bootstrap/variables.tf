variable "region" {
  description = "Region for the provider. IAM is global, so this only decides where the API calls go."
  type        = string
  default     = "us-west-2"
}

variable "github_repository" {
  description = "The repository, as owner/name, for the roles' descriptions. Access is decided by github_subject_prefix."
  type        = string
  default     = "vlad-radu-anca/private-clinical-ai"
}

variable "github_subject_prefix" {
  description = <<-EOT
    Start of the "sub" claim in GitHub's OIDC tokens for the repository. GitHub writes it with the owner's and
    the repository's numeric IDs ("immutable subject"), which survive renames and cannot be taken over by a new
    repository of the same name. Read it with:
      gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix
  EOT
  type        = string
  default     = "repo:vlad-radu-anca@87801278/private-clinical-ai@1457026145"

  validation {
    condition     = can(regex("^repo:[^/]+@[0-9]+/[^/]+@[0-9]+$", var.github_subject_prefix))
    error_message = "Expected repo:OWNER@OWNER_ID/REPO@REPO_ID, the immutable form GitHub sends."
  }
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
