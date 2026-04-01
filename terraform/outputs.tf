output "router_function_arn" {
  description = "ARN of the router Lambda function"
  value       = aws_lambda_function.router.arn
}

output "api_endpoint" {
  description = "Base URL for the webhook API"
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "stripe_webhook_url" {
  description = "Full URL for Stripe webhooks"
  value       = "${aws_apigatewayv2_api.main.api_endpoint}/webhooks/stripe"
}

output "shopify_webhook_url" {
  description = "Full URL for Shopify webhooks"
  value       = "${aws_apigatewayv2_api.main.api_endpoint}/webhooks/shopify"
}

output "github_webhook_url" {
  description = "Full URL for GitHub webhooks"
  value       = "${aws_apigatewayv2_api.main.api_endpoint}/webhooks/github"
}

output "generic_webhook_url" {
  description = "Full URL for generic/custom webhooks"
  value       = "${aws_apigatewayv2_api.main.api_endpoint}/webhooks/generic"
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table storing webhook events"
  value       = aws_dynamodb_table.events.name
}

output "stripe_queue_url" {
  description = "URL of the Stripe SQS queue"
  value       = aws_sqs_queue.stripe.url
}

output "shopify_queue_url" {
  description = "URL of the Shopify SQS queue"
  value       = aws_sqs_queue.shopify.url
}

output "github_queue_url" {
  description = "URL of the GitHub SQS queue"
  value       = aws_sqs_queue.github.url
}

output "generic_queue_url" {
  description = "URL of the generic SQS queue"
  value       = aws_sqs_queue.generic.url
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic for downstream notifications"
  value       = try(aws_sns_topic.events[0].arn, "")
}

output "stripe_processor_function_arn" {
  description = "ARN of the Stripe processor Lambda function"
  value       = aws_lambda_function.processor_stripe.arn
}

output "shopify_processor_function_arn" {
  description = "ARN of the Shopify processor Lambda function"
  value       = aws_lambda_function.processor_shopify.arn
}

output "github_processor_function_arn" {
  description = "ARN of the GitHub processor Lambda function"
  value       = aws_lambda_function.processor_github.arn
}

output "generic_processor_function_arn" {
  description = "ARN of the generic processor Lambda function"
  value       = aws_lambda_function.processor_generic.arn
}
