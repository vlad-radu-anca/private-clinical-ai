# One-time setup that lets GitHub Actions use this AWS account without stored keys. Each workflow run
# gets a short-lived token from GitHub, and AWS exchanges it for temporary credentials of one role,
# but only for this repository, and for the deploy role only from the protected environment.
#
#   plan role    pull requests: terraform plan, read-only, cannot even take the state lock
#   deploy role  the approved deploy-and-evaluate workflow: creates and destroys the stack

locals {
  oidc_url      = "token.actions.githubusercontent.com"
  oidc_provider = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
  name          = "clinical-ai-github"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_url}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://${local.oidc_url}"
}

# Trust: GitHub's token for this repository, in a given context ("sub"), meant for AWS ("aud").
data "aws_iam_policy_document" "trust" {
  for_each = {
    plan   = "${var.github_subject_prefix}:pull_request"
    deploy = "${var.github_subject_prefix}:environment:${var.deploy_environment}"
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_url}:sub"
      values   = [each.value]
    }
  }
}

# ---------- Plan role ----------
resource "aws_iam_role" "plan" {
  name                 = "${local.name}-plan"
  description          = "terraform plan on pull requests of ${var.github_repository}"
  assume_role_policy   = data.aws_iam_policy_document.trust["plan"].json
  max_session_duration = 3600
}

# Read access to describe every resource the stack manages; no write access anywhere.
resource "aws_iam_role_policy_attachment" "plan_read" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# State access spelled out, so the intent is visible: this project's state objects, read only. Plans run
# with -lock=false, so the role never needs to write the lock file.
data "aws_iam_policy_document" "plan_state" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.state_bucket}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.state_prefix}*"]
    }
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/${var.state_prefix}*"]
  }
}

resource "aws_iam_role_policy" "plan_state" {
  name   = "read-terraform-state"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan_state.json
}

# ---------- Deploy role ----------
# The stack creates IAM roles, KMS keys, a VPC, EKS, RDS and more, and then destroys all of it, so this
# role is an administrator. What limits it is who can use it: only workflow runs in the protected
# environment of this repository, after a reviewer approves, for at most four hours. The next step is a
# policy generated from a real run's CloudTrail activity (IAM Access Analyzer), see README.
resource "aws_iam_role" "deploy" {
  name                 = "${local.name}-deploy"
  description          = "Deploy, evaluate and destroy the stack from the ${var.deploy_environment} environment of ${var.github_repository}"
  assume_role_policy   = data.aws_iam_policy_document.trust["deploy"].json
  max_session_duration = 14400 # a deploy, evaluation and destroy takes about two hours; leave room
}

resource "aws_iam_role_policy_attachment" "deploy_admin" {
  role       = aws_iam_role.deploy.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
