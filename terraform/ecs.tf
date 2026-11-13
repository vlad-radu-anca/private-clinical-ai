# HAPI FHIR on ECS Fargate behind an internal ALB, plus a one-off loader task.
# (Phase 2 moves workloads to EKS next to the LLM; Fargate keeps phase 1 cheap and simple.)

# ---------- ECR (images are mirrored here; no Docker Hub access from the VPC) ----------
resource "aws_ecr_repository" "repo" {
  for_each             = toset(["hapi-fhir", "fhir-loader"])
  name                 = "${local.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.main.arn
  }
}

# ---------- Security groups ----------
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Internal ALB in front of HAPI"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "hapi" {
  name        = "${local.name}-hapi"
  description = "HAPI FHIR tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "client" {
  name        = "${local.name}-client"
  description = "Clients allowed to call the FHIR API (loader task, bastion)"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_clients" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.client.id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_hapi" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.hapi.id
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "hapi_from_alb" {
  security_group_id            = aws_security_group.hapi.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "hapi_to_db" {
  security_group_id            = aws_security_group.hapi.id
  referenced_security_group_id = aws_security_group.db.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

# Egress for HAPI and clients: only AWS endpoints (443 in VPC + S3 prefix list) and the ALB.
resource "aws_vpc_security_group_egress_rule" "https_vpc" {
  for_each          = { hapi = aws_security_group.hapi.id, client = aws_security_group.client.id }
  security_group_id = each.value
  cidr_ipv4         = var.vpc_cidr
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "https_s3" {
  for_each          = { hapi = aws_security_group.hapi.id, client = aws_security_group.client.id }
  security_group_id = each.value
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "client_to_alb" {
  security_group_id            = aws_security_group.client.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 80
  to_port                      = 80
  ip_protocol                  = "tcp"
}

# ---------- Internal ALB ----------
resource "aws_lb" "fhir" {
  name                       = "${local.name}-fhir"
  internal                   = true
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.private[*].id
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "hapi" {
  name        = "${local.name}-hapi"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id

  health_check {
    path                = "/fhir/metadata"
    matcher             = "200"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }
}

# Known gap (documented in README): plain HTTP inside the VPC. The load balancer is internal, in a VPC
# with no internet path, and only the loader and the SSM bastion can reach it. Next step: HTTPS with an
# ACM certificate for a name under a domain this account owns, then remove this exception.
#trivy:ignore:AWS-0054
#trivy:ignore:AVD-AWS-0054
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.fhir.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.hapi.arn
  }
}

# ---------- IAM ----------
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_db_instance.fhir.master_user_secret[0].secret_arn
      },
      {
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = aws_kms_key.main.arn
      }
    ]
  })
}

resource "aws_iam_role" "hapi_task" {
  name               = "${local.name}-hapi-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "hapi_exec" {
  # Allows ECS Exec for debugging, nothing else.
  role = aws_iam_role.hapi_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role" "loader_task" {
  name               = "${local.name}-loader-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "loader_read_s3" {
  role = aws_iam_role.loader_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "s3:ListBucket", Resource = aws_s3_bucket.data.arn },
      { Effect = "Allow", Action = "s3:GetObject", Resource = "${aws_s3_bucket.data.arn}/*" },
      { Effect = "Allow", Action = "kms:Decrypt", Resource = aws_kms_key.main.arn },
    ]
  })
}

# ---------- Logs ----------
resource "aws_cloudwatch_log_group" "ecs" {
  for_each          = toset(["hapi", "loader"])
  name              = "/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

# ---------- Cluster, task definitions, service ----------
resource "aws_ecs_cluster" "main" {
  name = local.name
}

resource "aws_ecs_task_definition" "hapi" {
  family                   = "${local.name}-hapi"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.hapi_cpu
  memory                   = var.hapi_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.hapi_task.arn

  container_definitions = jsonencode([{
    name         = "hapi"
    image        = "${aws_ecr_repository.repo["hapi-fhir"].repository_url}:${var.hapi_image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment = [
      { name = "SPRING_DATASOURCE_URL", value = "jdbc:postgresql://${aws_db_instance.fhir.address}:5432/hapi?sslmode=require" },
      { name = "SPRING_DATASOURCE_DRIVERCLASSNAME", value = "org.postgresql.Driver" },
      { name = "SPRING_JPA_PROPERTIES_HIBERNATE_DIALECT", value = "ca.uhn.fhir.jpa.model.dialect.HapiFhirPostgresDialect" },
      { name = "HAPI_FHIR_FHIR_VERSION", value = "R4" },
      { name = "JAVA_TOOL_OPTIONS", value = "-XX:MaxRAMPercentage=75" },
    ]
    secrets = [
      { name = "SPRING_DATASOURCE_USERNAME", valueFrom = "${aws_db_instance.fhir.master_user_secret[0].secret_arn}:username::" },
      { name = "SPRING_DATASOURCE_PASSWORD", valueFrom = "${aws_db_instance.fhir.master_user_secret[0].secret_arn}:password::" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs["hapi"].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "hapi"
      }
    }
  }])
}

resource "aws_ecs_service" "hapi" {
  name                              = "hapi"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.hapi.arn
  desired_count                     = var.hapi_desired_count
  launch_type                       = "FARGATE"
  enable_execute_command            = true
  health_check_grace_period_seconds = 300 # HAPI needs a few minutes on first start (schema creation)

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.hapi.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.hapi.arn
    container_name   = "hapi"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener.http, aws_vpc_endpoint.interface, aws_vpc_endpoint.s3]
}

resource "aws_ecs_task_definition" "loader" {
  family                   = "${local.name}-loader"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.loader_task.arn

  container_definitions = jsonencode([{
    name      = "loader"
    image     = "${aws_ecr_repository.repo["fhir-loader"].repository_url}:${var.loader_image_tag}"
    essential = true
    environment = [
      { name = "FHIR_URL", value = "http://${aws_lb.fhir.dns_name}/fhir" },
      { name = "AWS_DEFAULT_REGION", value = var.region },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs["loader"].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "loader"
      }
    }
  }])
}
