###############################################################################
# Webhook Router — Infrastructure
#
# Deploys:
#   - API Gateway HTTP API with routes for Stripe, Shopify, GitHub, Generic
#   - Router Lambda (validates signatures, normalizes, routes to SQS)
#   - Processor Lambdas for each source (reads from SQS, processes, stores in DynamoDB)
#   - SQS queues + DLQs per source for reliable async processing
#   - DynamoDB table for event storage
#   - SNS topic for downstream notifications
#   - SSM parameters for webhook secrets
#   - IAM roles + policies
#   - CloudWatch log groups and alarms
###############################################################################

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Owner       = "Three-Moons-Network"
    }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile name"
  type        = string
  default     = "default"
}

variable "project_name" {
  description = "Project identifier used in resource naming"
  type        = string
  default     = "webhook-router"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "uat", "prod"], var.environment)
    error_message = "Environment must be dev, uat, or prod."
  }
}

variable "stripe_secret" {
  description = "Stripe webhook signing secret"
  type        = string
  sensitive   = true
  default     = ""
}

variable "shopify_secret" {
  description = "Shopify webhook signing secret"
  type        = string
  sensitive   = true
  default     = ""
}

variable "github_secret" {
  description = "GitHub webhook signing secret"
  type        = string
  sensitive   = true
  default     = ""
}

variable "lambda_memory" {
  description = "Lambda memory in MB"
  type        = number
  default     = 256
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 60
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "sqs_retention_seconds" {
  description = "SQS message retention in seconds"
  type        = number
  default     = 345600 # 4 days
}

variable "enable_downstream_notifications" {
  description = "Enable SNS notifications for processed events"
  type        = bool
  default     = false
}

variable "dynamodb_billing_mode" {
  description = "DynamoDB billing mode (PAY_PER_REQUEST or PROVISIONED)"
  type        = string
  default     = "PAY_PER_REQUEST"
}

locals {
  prefix = "${var.project_name}-${var.environment}"
}

# ---------------------------------------------------------------------------
# SSM Parameters for Webhook Secrets
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "stripe_secret" {
  count       = var.stripe_secret != "" ? 1 : 0
  name        = "/${var.project_name}/${var.environment}/secrets/stripe"
  description = "Stripe webhook signing secret"
  type        = "SecureString"
  value       = var.stripe_secret

  tags = {
    Name = "${local.prefix}-stripe-secret"
  }
}

resource "aws_ssm_parameter" "shopify_secret" {
  count       = var.shopify_secret != "" ? 1 : 0
  name        = "/${var.project_name}/${var.environment}/secrets/shopify"
  description = "Shopify webhook signing secret"
  type        = "SecureString"
  value       = var.shopify_secret

  tags = {
    Name = "${local.prefix}-shopify-secret"
  }
}

resource "aws_ssm_parameter" "github_secret" {
  count       = var.github_secret != "" ? 1 : 0
  name        = "/${var.project_name}/${var.environment}/secrets/github"
  description = "GitHub webhook signing secret"
  type        = "SecureString"
  value       = var.github_secret

  tags = {
    Name = "${local.prefix}-github-secret"
  }
}

# ---------------------------------------------------------------------------
# DynamoDB Table for Event Storage
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "events" {
  name           = local.prefix
  billing_mode   = var.dynamodb_billing_mode
  hash_key       = "pk"
  range_key      = "sk"
  stream_enabled = false

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.environment == "prod" ? true : false
  }

  tags = {
    Name = "${local.prefix}-table"
  }
}

# ---------------------------------------------------------------------------
# SNS Topic for Downstream Notifications
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "events" {
  count             = var.enable_downstream_notifications ? 1 : 0
  name              = "${local.prefix}-events"
  display_name      = "Webhook Events"
  kms_master_key_id = "alias/aws/sns"

  tags = {
    Name = "${local.prefix}-topic"
  }
}

# ---------------------------------------------------------------------------
# SQS Queues + DLQs
# ---------------------------------------------------------------------------

resource "aws_sqs_queue" "stripe_dlq" {
  name                      = "${local.prefix}-stripe-dlq"
  message_retention_seconds = var.sqs_retention_seconds
}

resource "aws_sqs_queue" "stripe" {
  name                       = "${local.prefix}-stripe"
  message_retention_seconds  = var.sqs_retention_seconds
  visibility_timeout_seconds = var.lambda_timeout + 30
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.stripe_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "shopify_dlq" {
  name                      = "${local.prefix}-shopify-dlq"
  message_retention_seconds = var.sqs_retention_seconds
}

resource "aws_sqs_queue" "shopify" {
  name                       = "${local.prefix}-shopify"
  message_retention_seconds  = var.sqs_retention_seconds
  visibility_timeout_seconds = var.lambda_timeout + 30
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.shopify_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "github_dlq" {
  name                      = "${local.prefix}-github-dlq"
  message_retention_seconds = var.sqs_retention_seconds
}

resource "aws_sqs_queue" "github" {
  name                       = "${local.prefix}-github"
  message_retention_seconds  = var.sqs_retention_seconds
  visibility_timeout_seconds = var.lambda_timeout + 30
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.github_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "generic_dlq" {
  name                      = "${local.prefix}-generic-dlq"
  message_retention_seconds = var.sqs_retention_seconds
}

resource "aws_sqs_queue" "generic" {
  name                       = "${local.prefix}-generic"
  message_retention_seconds  = var.sqs_retention_seconds
  visibility_timeout_seconds = var.lambda_timeout + 30
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.generic_dlq.arn
    maxReceiveCount     = 3
  })
}

# ---------------------------------------------------------------------------
# IAM Roles and Policies
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# Router Lambda role
resource "aws_iam_role" "router" {
  name               = "${local.prefix}-router-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "router_permissions" {
  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.router.arn}:*"]
  }

  # SSM Parameter Store — read secrets
  statement {
    actions = ["ssm:GetParameter"]
    resources = [
      try(aws_ssm_parameter.stripe_secret[0].arn, "arn:aws:ssm:${var.aws_region}:*:parameter/${var.project_name}/${var.environment}/secrets/stripe"),
      try(aws_ssm_parameter.shopify_secret[0].arn, "arn:aws:ssm:${var.aws_region}:*:parameter/${var.project_name}/${var.environment}/secrets/shopify"),
      try(aws_ssm_parameter.github_secret[0].arn, "arn:aws:ssm:${var.aws_region}:*:parameter/${var.project_name}/${var.environment}/secrets/github"),
    ]
  }

  # SQS — send messages to queues
  statement {
    actions = [
      "sqs:SendMessage",
    ]
    resources = [
      aws_sqs_queue.stripe.arn,
      aws_sqs_queue.shopify.arn,
      aws_sqs_queue.github.arn,
      aws_sqs_queue.generic.arn,
    ]
  }
}

resource "aws_iam_role_policy" "router" {
  name   = "${local.prefix}-router-policy"
  role   = aws_iam_role.router.id
  policy = data.aws_iam_policy_document.router_permissions.json
}

# Processor Lambda role (shared for all processors)
resource "aws_iam_role" "processor" {
  name               = "${local.prefix}-processor-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "processor_permissions" {
  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "${aws_cloudwatch_log_group.processor_stripe.arn}:*",
      "${aws_cloudwatch_log_group.processor_shopify.arn}:*",
      "${aws_cloudwatch_log_group.processor_github.arn}:*",
      "${aws_cloudwatch_log_group.processor_generic.arn}:*",
    ]
  }

  # SQS — read from queues
  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [
      aws_sqs_queue.stripe.arn,
      aws_sqs_queue.shopify.arn,
      aws_sqs_queue.github.arn,
      aws_sqs_queue.generic.arn,
    ]
  }

  # DynamoDB — store events
  statement {
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
    ]
    resources = [aws_dynamodb_table.events.arn]
  }

  # SNS — send notifications (if enabled)
  dynamic "statement" {
    for_each = var.enable_downstream_notifications ? [1] : []
    content {
      actions = [
        "sns:Publish",
      ]
      resources = [aws_sns_topic.events[0].arn]
    }
  }
}

resource "aws_iam_role_policy" "processor" {
  name   = "${local.prefix}-processor-policy"
  role   = aws_iam_role.processor.id
  policy = data.aws_iam_policy_document.processor_permissions.json
}

# ---------------------------------------------------------------------------
# CloudWatch Log Groups
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "router" {
  name              = "/aws/lambda/${local.prefix}-router"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "processor_stripe" {
  name              = "/aws/lambda/${local.prefix}-processor-stripe"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "processor_shopify" {
  name              = "/aws/lambda/${local.prefix}-processor-shopify"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "processor_github" {
  name              = "/aws/lambda/${local.prefix}-processor-github"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "processor_generic" {
  name              = "/aws/lambda/${local.prefix}-processor-generic"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "api_gw" {
  name              = "/aws/apigateway/${local.prefix}"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------------------
# Lambda Functions
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "router" {
  function_name = "${local.prefix}-router"
  description   = "Validate webhook signatures and route to SQS"
  runtime       = "python3.11"
  handler       = "router.lambda_handler"
  memory_size   = var.lambda_memory
  timeout       = 30
  role          = aws_iam_role.router.arn

  filename         = "${path.module}/../dist/router.zip"
  source_code_hash = fileexists("${path.module}/../dist/router.zip") ? filebase64sha256("${path.module}/../dist/router.zip") : null

  environment {
    variables = {
      ENVIRONMENT       = var.environment
      STRIPE_QUEUE_URL  = aws_sqs_queue.stripe.url
      SHOPIFY_QUEUE_URL = aws_sqs_queue.shopify.url
      GITHUB_QUEUE_URL  = aws_sqs_queue.github.url
      GENERIC_QUEUE_URL = aws_sqs_queue.generic.url
      LOG_LEVEL         = var.environment == "prod" ? "WARNING" : "INFO"
    }
  }

  depends_on = [
    aws_iam_role_policy.router,
    aws_cloudwatch_log_group.router,
  ]
}

# Processor: Stripe
resource "aws_lambda_function" "processor_stripe" {
  function_name = "${local.prefix}-processor-stripe"
  description   = "Process Stripe webhook events"
  runtime       = "python3.11"
  handler       = "stripe.lambda_handler"
  memory_size   = var.lambda_memory
  timeout       = var.lambda_timeout
  role          = aws_iam_role.processor.arn

  filename         = "${path.module}/../dist/stripe.zip"
  source_code_hash = fileexists("${path.module}/../dist/stripe.zip") ? filebase64sha256("${path.module}/../dist/stripe.zip") : null

  environment {
    variables = {
      ENVIRONMENT    = var.environment
      DYNAMODB_TABLE = aws_dynamodb_table.events.name
      SNS_TOPIC_ARN  = var.enable_downstream_notifications ? aws_sns_topic.events[0].arn : ""
      LOG_LEVEL      = var.environment == "prod" ? "WARNING" : "INFO"
    }
  }

  depends_on = [
    aws_iam_role_policy.processor,
    aws_cloudwatch_log_group.processor_stripe,
  ]
}

# Processor: Shopify
resource "aws_lambda_function" "processor_shopify" {
  function_name = "${local.prefix}-processor-shopify"
  description   = "Process Shopify webhook events"
  runtime       = "python3.11"
  handler       = "shopify.lambda_handler"
  memory_size   = var.lambda_memory
  timeout       = var.lambda_timeout
  role          = aws_iam_role.processor.arn

  filename         = "${path.module}/../dist/shopify.zip"
  source_code_hash = fileexists("${path.module}/../dist/shopify.zip") ? filebase64sha256("${path.module}/../dist/shopify.zip") : null

  environment {
    variables = {
      ENVIRONMENT    = var.environment
      DYNAMODB_TABLE = aws_dynamodb_table.events.name
      SNS_TOPIC_ARN  = var.enable_downstream_notifications ? aws_sns_topic.events[0].arn : ""
      LOG_LEVEL      = var.environment == "prod" ? "WARNING" : "INFO"
    }
  }

  depends_on = [
    aws_iam_role_policy.processor,
    aws_cloudwatch_log_group.processor_shopify,
  ]
}

# Processor: GitHub
resource "aws_lambda_function" "processor_github" {
  function_name = "${local.prefix}-processor-github"
  description   = "Process GitHub webhook events"
  runtime       = "python3.11"
  handler       = "github.lambda_handler"
  memory_size   = var.lambda_memory
  timeout       = var.lambda_timeout
  role          = aws_iam_role.processor.arn

  filename         = "${path.module}/../dist/github.zip"
  source_code_hash = fileexists("${path.module}/../dist/github.zip") ? filebase64sha256("${path.module}/../dist/github.zip") : null

  environment {
    variables = {
      ENVIRONMENT    = var.environment
      DYNAMODB_TABLE = aws_dynamodb_table.events.name
      SNS_TOPIC_ARN  = var.enable_downstream_notifications ? aws_sns_topic.events[0].arn : ""
      LOG_LEVEL      = var.environment == "prod" ? "WARNING" : "INFO"
    }
  }

  depends_on = [
    aws_iam_role_policy.processor,
    aws_cloudwatch_log_group.processor_github,
  ]
}

# Processor: Generic
resource "aws_lambda_function" "processor_generic" {
  function_name = "${local.prefix}-processor-generic"
  description   = "Process generic/custom webhook events"
  runtime       = "python3.11"
  handler       = "generic.lambda_handler"
  memory_size   = var.lambda_memory
  timeout       = var.lambda_timeout
  role          = aws_iam_role.processor.arn

  filename         = "${path.module}/../dist/generic.zip"
  source_code_hash = fileexists("${path.module}/../dist/generic.zip") ? filebase64sha256("${path.module}/../dist/generic.zip") : null

  environment {
    variables = {
      ENVIRONMENT    = var.environment
      DYNAMODB_TABLE = aws_dynamodb_table.events.name
      SNS_TOPIC_ARN  = var.enable_downstream_notifications ? aws_sns_topic.events[0].arn : ""
      LOG_LEVEL      = var.environment == "prod" ? "WARNING" : "INFO"
    }
  }

  depends_on = [
    aws_iam_role_policy.processor,
    aws_cloudwatch_log_group.processor_generic,
  ]
}

# ---------------------------------------------------------------------------
# Lambda Event Source Mappings (SQS → Lambda)
# ---------------------------------------------------------------------------

resource "aws_lambda_event_source_mapping" "stripe" {
  event_source_arn = aws_sqs_queue.stripe.arn
  function_name    = aws_lambda_function.processor_stripe.function_name
  batch_size       = 10
}

resource "aws_lambda_event_source_mapping" "shopify" {
  event_source_arn = aws_sqs_queue.shopify.arn
  function_name    = aws_lambda_function.processor_shopify.function_name
  batch_size       = 10
}

resource "aws_lambda_event_source_mapping" "github" {
  event_source_arn = aws_sqs_queue.github.arn
  function_name    = aws_lambda_function.processor_github.function_name
  batch_size       = 10
}

resource "aws_lambda_event_source_mapping" "generic" {
  event_source_arn = aws_sqs_queue.generic.arn
  function_name    = aws_lambda_function.processor_generic.function_name
  batch_size       = 10
}

# ---------------------------------------------------------------------------
# API Gateway HTTP API
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "main" {
  name          = "${local.prefix}-api"
  protocol_type = "HTTP"
  description   = "Webhook router API"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "router" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.router.invoke_arn
  payload_format_version = "2.0"
}

# Routes for each webhook source
resource "aws_apigatewayv2_route" "stripe" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /webhooks/stripe"
  target    = "integrations/${aws_apigatewayv2_integration.router.id}"
}

resource "aws_apigatewayv2_route" "shopify" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /webhooks/shopify"
  target    = "integrations/${aws_apigatewayv2_integration.router.id}"
}

resource "aws_apigatewayv2_route" "github" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /webhooks/github"
  target    = "integrations/${aws_apigatewayv2_integration.router.id}"
}

resource "aws_apigatewayv2_route" "generic" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /webhooks/generic"
  target    = "integrations/${aws_apigatewayv2_integration.router.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = 100
    throttling_burst_limit = 200
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      method         = "$context.httpMethod"
      path           = "$context.path"
      status         = "$context.status"
      latency        = "$context.responseLatency"
      integrationErr = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "api_gw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.router.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

# ---------------------------------------------------------------------------
# CloudWatch Alarms
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "router_errors" {
  alarm_name          = "${local.prefix}-router-errors"
  alarm_description   = "Router Lambda error rate exceeded"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.router.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "sqs_dlq_messages" {
  alarm_name          = "${local.prefix}-sqs-dlq-messages"
  alarm_description   = "Messages in DLQ indicate processing failures"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.stripe_dlq.name
  }
}
