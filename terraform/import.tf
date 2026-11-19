# The one way third-party artefacts enter the platform. The VPC has no internet path, so a short
# CodeBuild job, outside the VPC, copies pinned container images into ECR and pinned model weights
# into the encrypted bucket. Everything the cluster then runs was imported deliberately, at a known
# digest or revision, and scanned by ECR on push.
#
#   aws codebuild start-build --project-name <import_project output>    (or: make aws-import)
#
# Re-running is cheap: images already in ECR and models already in S3 are skipped.

locals {
  images = {
    vllm = { source = "vllm/vllm-openai", tag = "v0.34.0", digest = "sha256:20fdb089454afbb324efa4110a2c025ff7b27cc1a086e07aa580b18df8ad1ce1" }
    tei  = { source = "ghcr.io/huggingface/text-embeddings-inference", tag = "cpu-1.9.4", digest = "sha256:2538ea1c9640d3763b15af668039d24172d063b42337b0c27796fc2be180c78d" }
    # GPU build, used only while bulk indexing: on CPU, ~24,000 chunks take hours. The universal "cuda"
    # build, not a per-generation one: the node can be an L4 (g6, Ada) or, when g6 has no capacity, an
    # A10G (g5, Ampere), and the Ada-only "89" build refused to start on the A10G.
    tei-gpu   = { source = "ghcr.io/huggingface/text-embeddings-inference", tag = "cuda-1.9.4", digest = "sha256:c831a5cedb89d62f7b0fdb59e2d73e8c30e10c1d5006897640e2ce58a71cedba" }
    hapi-fhir = { source = "hapiproject/hapi", tag = "v8.12.0-2", digest = "sha256:0ff86aac4c7bd13a1ee226444a70263bb46ed1c28ef543c30a44f865b80ca04e" }
  }
  models = {
    qwen3-8b             = { repo = "Qwen/Qwen3-8B", revision = "b968826d9c46dd6066d109eabc6255188de91218" }
    qwen3-embedding-0_6b = { repo = "Qwen/Qwen3-Embedding-0.6B", revision = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3" }
  }
}

resource "aws_cloudwatch_log_group" "import" {
  name              = "/${local.name}/import"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

resource "aws_iam_role" "import" {
  name = "${local.name}-import"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "import" {
  role = aws_iam_role.import.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.import.arn}:*" },
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:DescribeImages",
        ]
        Resource = [for name in keys(local.images) : aws_ecr_repository.repo[name].arn]
      },
      { Effect = "Allow", Action = "s3:ListBucket", Resource = aws_s3_bucket.data.arn },
      { Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"], Resource = "${aws_s3_bucket.data.arn}/models/*" },
      { Effect = "Allow", Action = ["kms:GenerateDataKey", "kms:Decrypt"], Resource = aws_kms_key.main.arn },
    ]
  })
}

resource "aws_codebuild_project" "import" {
  name           = "${local.name}-import"
  description    = "Copy pinned container images to ECR and pinned model weights to S3"
  service_role   = aws_iam_role.import.arn
  build_timeout  = 60
  encryption_key = aws_kms_key.main.arn

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    # Large: 128 GB of disk for the vLLM image and 17.6 GB of model files. Privileged: runs Docker.
    compute_type    = "BUILD_GENERAL1_LARGE"
    image           = "aws/codebuild/amazonlinux-x86_64-standard:5.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.import.name
    }
  }

  source {
    type = "NO_SOURCE"
    buildspec = templatefile("${path.module}/import-buildspec.yml.tftpl", {
      region   = var.region
      registry = split("/", aws_ecr_repository.repo["vllm"].repository_url)[0]
      repos    = { for name in keys(local.images) : name => aws_ecr_repository.repo[name].repository_url }
      images   = local.images
      models   = local.models
      bucket   = aws_s3_bucket.data.bucket
      kms_key  = aws_kms_key.main.arn
    })
  }
}
