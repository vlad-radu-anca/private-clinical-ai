resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "PostgreSQL reachable only from the HAPI tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_hapi" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.hapi.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_db_instance" "fhir" {
  identifier     = "${local.name}-fhir"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class
  db_name        = "hapi"
  username       = "hapi_admin"

  # Password generated and stored by RDS in Secrets Manager, never in Terraform state.
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.main.arn

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.main.arn

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = false # demo; true in production

  backup_retention_period         = 1
  enabled_cloudwatch_logs_exports = ["postgresql"]
  auto_minor_version_upgrade      = true

  # Demo settings, so `terraform destroy` is fast and free. Flip for production.
  deletion_protection = false
  skip_final_snapshot = true
}
