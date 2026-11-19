output "plan_role_arn" {
  description = "Set as the repository variable AWS_PLAN_ROLE_ARN."
  value       = aws_iam_role.plan.arn
}

output "deploy_role_arn" {
  description = "Set as the variable AWS_DEPLOY_ROLE_ARN of the GitHub environment."
  value       = aws_iam_role.deploy.arn
}

output "github_setup" {
  description = "The GitHub settings the workflows read, ready to paste into gh commands."
  value = {
    repository_variables = {
      AWS_PLAN_ROLE_ARN = aws_iam_role.plan.arn
      TF_STATE_BUCKET   = var.state_bucket
    }
    environment           = var.deploy_environment
    environment_variables = { AWS_DEPLOY_ROLE_ARN = aws_iam_role.deploy.arn }
  }
}
