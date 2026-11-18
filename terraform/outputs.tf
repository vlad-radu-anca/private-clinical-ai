output "data_bucket" {
  description = "KMS-encrypted bucket holding the Synthea bundles."
  value       = aws_s3_bucket.data.bucket
}

output "kms_key_arn" {
  description = "Customer-managed key encrypting every data store in the stack."
  value       = aws_kms_key.main.arn
}

output "ecr_hapi" {
  description = "ECR repository for the mirrored HAPI FHIR image."
  value       = aws_ecr_repository.repo["hapi-fhir"].repository_url
}

output "ecr_loader" {
  description = "ECR repository for the loader image."
  value       = aws_ecr_repository.repo["fhir-loader"].repository_url
}

output "fhir_internal_url" {
  description = "FHIR API base URL, reachable only from inside the VPC."
  value       = "http://${aws_lb.fhir.dns_name}/fhir"
}

output "alb_dns" {
  description = "DNS name of the internal load balancer."
  value       = aws_lb.fhir.dns_name
}

output "ecs_cluster" {
  description = "ECS cluster running HAPI and the loader."
  value       = aws_ecs_cluster.main.name
}

output "loader_task_definition" {
  description = "Task definition family of the one-off loader task."
  value       = aws_ecs_task_definition.loader.family
}

output "private_subnets" {
  description = "Private subnets for one-off tasks."
  value       = aws_subnet.private[*].id
}

output "client_security_group" {
  description = "Security group for clients allowed to call the FHIR API."
  value       = aws_security_group.client.id
}

output "bastion_id" {
  description = "Instance ID of the SSM-only bastion."
  value       = aws_instance.bastion.id
}

output "port_forward_command" {
  description = "Run locally, then open http://localhost:8080/fhir/metadata"
  value       = "aws ssm start-session --target ${aws_instance.bastion.id} --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters host=${aws_lb.fhir.dns_name},portNumber=80,localPortNumber=8080"
}

# ---------- Phase 2 ----------
output "eks_cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.main.name
}

output "kubeconfig_command" {
  description = "Adds the cluster to your kubeconfig."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.main.name}"
}

output "import_project" {
  description = "CodeBuild project that imports pinned images and models."
  value       = aws_codebuild_project.import.name
}

output "models_uri" {
  description = "S3 prefix holding the imported model weights."
  value       = "s3://${aws_s3_bucket.data.bucket}/models/"
}

output "db_host" {
  description = "RDS endpoint, used by the RAG API for the pgvector index."
  value       = aws_db_instance.fhir.address
}

output "db_secret_arn" {
  description = "RDS-managed secret the RAG API reads through Pod Identity."
  value       = aws_db_instance.fhir.master_user_secret[0].secret_arn
}

output "ecr_repositories" {
  description = "ECR repository URLs by name."
  value       = { for name, repo in aws_ecr_repository.repo : name => repo.repository_url }
}

output "region" {
  description = "AWS region of the stack."
  value       = var.region
}

output "imported_image_tags" {
  description = "Tags of the images the import job mirrors, by repository name."
  value       = { for name, image in local.images : name => image.tag }
}
