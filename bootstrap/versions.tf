terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Same state bucket as the stack, its own key:
  #   terraform -chdir=bootstrap init -backend-config=../terraform/backend.hcl -backend-config=key=private-clinical-ai/bootstrap.tfstate
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "clinical-ai"
      Component = "ci-bootstrap"
      ManagedBy = "terraform"
    }
  }
}
