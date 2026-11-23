output "plan_role_arn" {
  description = "Set as the repository secret AWS_PLAN_ROLE_ARN."
  value       = aws_iam_role.plan.arn
}

output "deploy_role_arn" {
  description = "Set as the secret AWS_DEPLOY_ROLE_ARN of the GitHub environment."
  value       = aws_iam_role.deploy.arn
}

# Secrets rather than variables: none of these is a credential (only GitHub's OIDC token for this repository
# can assume the roles), but they name the account, roles and bucket, and a public repository's variables
# are readable in its logs, secrets are masked.
output "github_setup" {
  description = "The GitHub settings the workflows read, ready to paste into gh secret set."
  value = {
    repository_secrets  = { AWS_PLAN_ROLE_ARN = aws_iam_role.plan.arn, TF_STATE_BUCKET = var.state_bucket }
    environment         = var.deploy_environment
    environment_secrets = { AWS_DEPLOY_ROLE_ARN = aws_iam_role.deploy.arn, TF_STATE_BUCKET = var.state_bucket }
  }
}
