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
