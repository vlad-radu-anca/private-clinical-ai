# Phase 2: EKS in the same private VPC. Two node groups:
#   system  Bottlerocket, CPU: RAG API, embeddings server (TEI), indexer and evaluation jobs
#   gpu     Bottlerocket NVIDIA, one g6.xlarge (NVIDIA L4, 24 GB): vLLM serving the chat model
# Bottlerocket is a minimal, immutable OS for containers. Its NVIDIA variant ships the driver (580
# series, needed by vLLM's CUDA 13 build) and the Kubernetes device plugin, so nothing GPU-related
# has to be installed into the cluster.

locals {
  cluster_name = local.name
  # Service accounts that get AWS permissions through EKS Pod Identity.
  app_namespace = "clinical-ai"
}

# ---------- Control plane ----------
resource "aws_iam_role" "eks_cluster" {
  name = "${local.name}-eks-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# Created before the cluster, so it is encrypted and has a retention period. EKS would otherwise
# create it with neither.
resource "aws_cloudwatch_log_group" "eks" {
  name              = "/aws/eks/${local.cluster_name}/cluster"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

resource "aws_eks_cluster" "main" {
  name     = local.cluster_name
  version  = var.eks_version
  role_arn = aws_iam_role.eks_cluster.arn

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }

  vpc_config {
    subnet_ids              = aws_subnet.private[*].id
    endpoint_private_access = true
    # The API is also reachable from one address, so kubectl works from the operator's laptop. Nodes,
    # pods, data and model traffic stay inside the VPC either way. For a fully private API, set
    # endpoint_public_access = false and run kubectl through the SSM bastion.
    endpoint_public_access = true
    public_access_cidrs    = [var.admin_cidr]
  }

  encryption_config {
    provider {
      key_arn = aws_kms_key.main.arn
    }
    resources = ["secrets"]
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  depends_on = [aws_iam_role_policy_attachment.eks_cluster, aws_cloudwatch_log_group.eks]
}

# Whoever runs Terraform gets cluster admin, through an access entry rather than the aws-auth ConfigMap.
# Access entries need an IAM user or role ARN. When Terraform runs under an assumed role (a CI pipeline
# through OIDC, or SSO), the caller ARN is a temporary session ARN, so it is resolved to the role first.
data "aws_iam_session_context" "current" {
  arn = data.aws_caller_identity.current.arn
}

resource "aws_eks_access_entry" "admin" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = data.aws_iam_session_context.current.issuer_arn
}

resource "aws_eks_access_policy_association" "admin" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = aws_eks_access_entry.admin.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope {
    type = "cluster"
  }
}

# Pods reach the database and HAPI's internal load balancer from the cluster security group, which
# EKS attaches to every node.
resource "aws_vpc_security_group_ingress_rule" "db_from_eks" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  description                  = "PostgreSQL from EKS pods (RAG API, indexer)"
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_eks" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
  description                  = "FHIR API from EKS pods (indexer, evaluation)"
}

# ---------- Nodes ----------
resource "aws_iam_role" "eks_node" {
  name = "${local.name}-eks-node"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_node" {
  for_each = toset([
    "AmazonEKSWorkerNodePolicy",
    "AmazonEKS_CNI_Policy",
    "AmazonEC2ContainerRegistryPullOnly",
    "AmazonSSMManagedInstanceCore", # Session Manager into Bottlerocket's control container, instead of SSH
  ])
  role       = aws_iam_role.eks_node.name
  policy_arn = "arn:aws:iam::aws:policy/${each.value}"
}

# Bottlerocket keeps the OS on one small volume and container images, model files and pod storage on
# a second one (xvdb). The GPU node needs room for the vLLM image (~10 GB) and the model (16.4 GB).
resource "aws_launch_template" "node" {
  for_each = { system = 40, gpu = 100 }
  name     = "${local.name}-${each.key}"

  block_device_mappings {
    device_name = "/dev/xvdb"
    ebs {
      volume_size           = each.value
      volume_type           = "gp3"
      encrypted             = true # the account's EBS key: a customer key would need the Auto Scaling role in its policy
      delete_on_termination = true
    }
  }

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1 # pods cannot reach the node's instance metadata, only Pod Identity
  }

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.name}-${each.key}" }
  }
}

resource "aws_eks_node_group" "system" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "system"
  node_role_arn   = aws_iam_role.eks_node.arn
  subnet_ids      = aws_subnet.private[*].id
  ami_type        = "BOTTLEROCKET_x86_64"
  instance_types  = [var.system_instance_type]
  capacity_type   = "ON_DEMAND"

  scaling_config {
    desired_size = 1
    min_size     = 1
    max_size     = 2
  }

  launch_template {
    id      = aws_launch_template.node["system"].id
    version = aws_launch_template.node["system"].latest_version
  }

  labels = { workload = "system" }

  depends_on = [aws_iam_role_policy_attachment.eks_node, aws_vpc_endpoint.interface, aws_vpc_endpoint.s3]
}

resource "aws_eks_node_group" "gpu" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "gpu"
  node_role_arn   = aws_iam_role.eks_node.arn
  # Every zone in the region, so the node starts wherever a GPU is available (InsufficientInstanceCapacity).
  subnet_ids     = concat(aws_subnet.private[*].id, aws_subnet.gpu[*].id)
  ami_type       = "BOTTLEROCKET_x86_64_NVIDIA"
  instance_types = [var.gpu_instance_type]
  capacity_type  = "ON_DEMAND"

  # gpu_node_count = 0 stops the GPU, the largest cost, without destroying anything else. The
  # account's GPU quota allows exactly one node, so the group can never grow past one.
  scaling_config {
    desired_size = var.gpu_node_count
    min_size     = 0
    max_size     = 1
  }

  update_config {
    max_unavailable = 1
  }

  # Only pods that tolerate this taint (vLLM) are scheduled on the GPU node.
  taint {
    key    = "nvidia.com/gpu"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  launch_template {
    id      = aws_launch_template.node["gpu"].id
    version = aws_launch_template.node["gpu"].latest_version
  }

  labels = { workload = "gpu" }

  depends_on = [aws_iam_role_policy_attachment.eks_node, aws_vpc_endpoint.interface, aws_vpc_endpoint.s3]
}

# ---------- Add-ons ----------
# Versions default to the ones EKS recommends for the cluster's Kubernetes version. Their images come
# from AWS's own registries in the region, reachable through the ECR endpoints.
resource "aws_eks_addon" "addon" {
  for_each     = toset(["vpc-cni", "kube-proxy", "coredns", "eks-pod-identity-agent"])
  cluster_name = aws_eks_cluster.main.name
  addon_name   = each.value

  depends_on = [aws_eks_node_group.system]
}

# ---------- Pod Identity: AWS permissions per service account ----------
locals {
  pod_identities = {
    # Init containers that copy model files from S3 before vLLM and TEI start.
    model-fetch = {
      statements = [
        { Action = "s3:ListBucket", Resource = aws_s3_bucket.data.arn, Condition = { StringLike = { "s3:prefix" = ["models/*"] } } },
        { Action = "s3:GetObject", Resource = "${aws_s3_bucket.data.arn}/models/*" },
        { Action = "kms:Decrypt", Resource = aws_kms_key.main.arn },
      ]
    }
    # RAG API, indexer and evaluation: read the database password, nothing else.
    rag = {
      statements = [
        { Action = "secretsmanager:GetSecretValue", Resource = aws_db_instance.fhir.master_user_secret[0].secret_arn },
        { Action = "kms:Decrypt", Resource = aws_kms_key.main.arn },
      ]
    }
  }
}

resource "aws_iam_role" "pod" {
  for_each = local.pod_identities
  name     = "${local.name}-pod-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy" "pod" {
  for_each = local.pod_identities
  role     = aws_iam_role.pod[each.key].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [for s in each.value.statements : merge({ Effect = "Allow" }, s)]
  })
}

resource "aws_eks_pod_identity_association" "pod" {
  for_each        = local.pod_identities
  cluster_name    = aws_eks_cluster.main.name
  namespace       = local.app_namespace
  service_account = each.key
  role_arn        = aws_iam_role.pod[each.key].arn
}
