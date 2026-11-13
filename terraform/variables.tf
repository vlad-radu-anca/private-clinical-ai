variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "clinical-ai"
}

variable "environment" {
  description = "Environment name, part of every resource name."
  type        = string
  default     = "dev"
}

variable "region" {
  description = "EU region keeps data residency simple (GDPR)."
  type        = string
  default     = "eu-central-1"
}

variable "vpc_cidr" {
  description = "CIDR block of the private VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "endpoint_az_count" {
  description = "AZs that get interface endpoints. 1 halves endpoint cost (fine for a demo); use 2 for HA."
  type        = number
  default     = 1
}

variable "db_instance_class" {
  description = "RDS instance class for the HAPI database."
  type        = string
  default     = "db.t4g.small"
}

variable "hapi_image_tag" {
  description = "Tag of the HAPI image mirrored into ECR by scripts/push-images.sh, for example v8.12.0-2"
  type        = string
}

variable "hapi_cpu" {
  description = "Fargate CPU units for the HAPI task (1024 = 1 vCPU)."
  type        = number
  default     = 1024
}

variable "hapi_memory" {
  description = "Fargate memory for the HAPI task, in MiB."
  type        = number
  default     = 4096
}

variable "hapi_desired_count" {
  description = "Set to 0 to stop the FHIR server (and its Fargate cost) without destroying anything."
  type        = number
  default     = 1
}

variable "loader_image_tag" {
  description = "Tag of the loader image built and pushed by scripts/push-images.sh."
  type        = string
  default     = "v1"
}

variable "log_retention_days" {
  description = "Retention for the ECS and VPC flow log groups."
  type        = number
  default     = 30
}
