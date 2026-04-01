# Webhook Router

Production-ready system for receiving, validating, and processing webhooks from multiple external services. Routes webhooks from Stripe, Shopify, and GitHub to reliable SQS queues for async processing, with built-in signature validation, payload normalization, and event storage in DynamoDB.

Built as a reference implementation by [Three Moons Network](https://threemoonsnetwork.net) — an AI consulting practice helping small businesses build production-grade integrations.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud                                       │
│                                                                              │
│  External Services                                                           │
│         │                                                                   │
│         ├─ Stripe                                                           │
│         ├─ Shopify                                                          │
│         ├─ GitHub                                                           │
│         └─ Custom/Generic                                                   │
│         │                                                                   │
│         ▼                                                                   │
│   API Gateway (HTTP API)                                                    │
│   ├─ POST /webhooks/stripe   ─────┐                                        │
│   ├─ POST /webhooks/shopify  ─────┤                                        │
│   ├─ POST /webhooks/github   ─────┤                                        │
│   └─ POST /webhooks/generic  ─────┤                                        │
│                                    │                                        │
│                                    ▼                                        │
│                          ┌──────────────────────┐                          │
│                          │  Router Lambda       │                          │
│                          │ ┌──────────────────┐ │                          │
│                          │ │ 1. Validate sig  │ │                          │
│                          │ │ 2. Normalize     │ │                          │
│                          │ │ 3. Route to SQS  │ │                          │
│                          │ └──────────────────┘ │                          │
│                          └──────────────────────┘                          │
│                                    │                                        │
│         ┌──────────────────────────┼──────────────────────────┐            │
│         │                          │                          │            │
│         ▼                          ▼                          ▼            │
│    SQS: Stripe              SQS: Shopify              SQS: GitHub         │
│    │                        │                         │                   │
│    └── DLQ                  └──DLQ                    └──DLQ               │
│    │                        │                        │                    │
│    ▼                        ▼                        ▼                    │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐               │
│  │ Processor:   │    │ Processor:   │    │ Processor:   │               │
│  │ Stripe       │    │ Shopify      │    │ GitHub       │               │
│  │              │    │              │    │              │               │
│  │ • Normalize  │    │ • Normalize  │    │ • Normalize  │               │
│  │ • Store      │    │ • Store      │    │ • Store      │               │
│  │ • Notify SNS │    │ • Notify SNS │    │ • Notify SNS │               │
│  └──────────────┘    └──────────────┘    └──────────────┘               │
│         │                   │                   │                        │
│         └───────────────────┼───────────────────┘                        │
│                             ▼                                             │
│                     DynamoDB Table                                         │
│                 (Event Storage & History)                                  │
│                             │                                             │
│                             └──▶ SNS Topic (optional)                    │
│                                  (Downstream processing)                  │
│                                                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

## What It Does

**Webhook ingestion and routing:**
- HTTP API with dedicated endpoints for each webhook source
- Validates webhook signatures (Stripe, Shopify, GitHub) using secrets from SSM
- Normalizes payloads into a common event schema
- Routes to SQS queues for reliable async processing

**Event processing:**
- Dedicated processor Lambda for each source (Stripe, Shopify, GitHub, Generic)
- Extracts key fields from vendor-specific payloads
- Stores normalized events in DynamoDB for audit trail and replay
- Sends downstream SNS notifications (optional)

**Reliability and observability:**
- Dead Letter Queues (DLQs) for failed events
- 90-day event retention in DynamoDB with TTL
- CloudWatch logging and alarms for errors and DLQ activity
- Batch failure handling with partial success support

## Supported Webhooks

| Source | Events | Notes |
|--------|--------|-------|
| **Stripe** | `payment_intent.succeeded`, `invoice.paid`, `charge.failed` | Signature validation required |
| **Shopify** | `orders/create`, `orders/fulfilled`, `products/update` | Signature validation required |
| **GitHub** | `push`, `pull_request`, `issues` | Signature validation required |
| **Generic** | Custom webhooks | No signature validation; catch-all endpoint |

## Quick Start

### Prerequisites

- AWS account with CLI configured
- Terraform >= 1.5
- Python 3.11+
- Webhook signing secrets (from your provider's settings)

### 1. Clone and configure

```bash
git clone git@github.com:Three-Moons-Network/webhook-router.git
cd webhook-router
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit terraform.tfvars with your webhook secrets
```

### 2. Get webhook secrets

**Stripe:**
1. Go to Developers > Webhooks > Add endpoint
2. Set endpoint URL to your API Gateway URL (we'll get it after deploy)
3. Copy the Signing secret (`whsec_...`)

**Shopify:**
1. Go to Settings > Apps and integrations > Develop apps
2. Create a webhook and note the API credentials secret

**GitHub:**
1. Go to Settings > Developer settings > Webhooks
2. Create a webhook and copy the Secret

### 3. Build Lambda packages

```bash
./scripts/deploy.sh
```

### 4. Deploy infrastructure

```bash
cd terraform
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

Terraform outputs the webhook URLs.

### 5. Configure webhooks in your providers

**Stripe:**
```
POST https://<api-id>.execute-api.us-east-1.amazonaws.com/webhooks/stripe
```

**Shopify:**
```
POST https://<api-id>.execute-api.us-east-1.amazonaws.com/webhooks/shopify
```

**GitHub:**
```
POST https://<api-id>.execute-api.us-east-1.amazonaws.com/webhooks/github
```

### 6. Monitor webhooks

```bash
# Get API endpoint
API_URL=$(terraform output -raw api_endpoint)

# Send test webhook (if your provider supports it)
curl -X POST "$API_URL/webhooks/generic" \
  -H "Content-Type: application/json" \
  -d '{"type": "test", "id": "evt_123", "timestamp": 1234567890}'

# Check DynamoDB table
aws dynamodb scan --table-name webhook-router-dev --region us-east-1
```

### 7. Tear down

```bash
terraform destroy
```

## Project Structure

```
├── src/
│   ├── router.py                 # API Gateway handler (validates, routes)
│   └── processors/
│       ├── stripe.py             # Stripe event processor
│       ├── shopify.py            # Shopify event processor
│       ├── github.py             # GitHub event processor
│       └── generic.py            # Generic/custom event processor
├── tests/
│   ├── test_router.py            # Router unit tests
│   └── test_processors.py        # Processor unit tests
├── terraform/
│   ├── main.tf                   # All infra: API GW, Lambdas, SQS, DynamoDB, SNS
│   ├── outputs.tf                # Webhook URLs, queue URLs, DynamoDB table name
│   ├── backend.tf                # Remote state config (commented for local use)
│   └── terraform.tfvars.example
├── scripts/
│   └── deploy.sh                 # Build Lambda packages
├── .github/workflows/
│   └── ci.yml                    # Test, lint, TF validate, package
├── requirements.txt              # Runtime: boto3
└── requirements-dev.txt          # Dev: pytest, ruff, moto
```

## Infrastructure Details

| Resource | Purpose | Configuration |
|----------|---------|---|
| API Gateway HTTP API | REST endpoints for each webhook source | CORS enabled, throttled (100 req/s, 200 burst) |
| Router Lambda | Validates signatures, normalizes, routes to SQS | 256MB / 30s timeout |
| Processor Lambdas (4x) | Process events from SQS queues | 256MB / 60s timeout each |
| SQS Queues (4x) | Reliable async processing with DLQs | 4-day retention, 3x retry limit |
| DynamoDB Table | Event storage and audit trail | PAY_PER_REQUEST billing, 90-day TTL |
| SNS Topic | Downstream notifications (optional) | KMS encrypted |
| SSM Parameters | Webhook signing secrets | SecureString encryption |
| CloudWatch Logs | Logs for all Lambdas and API Gateway | 30-day retention (configurable) |
| CloudWatch Alarms | Monitor errors and DLQ activity | Proactive failure detection |
| IAM Roles | Least-privilege access | Separate roles for router and processors |

All resources are tagged with `Project`, `Environment`, `ManagedBy`, and `Owner`.

## Configuration

Edit `terraform/terraform.tfvars`:

```hcl
environment                      = "dev"              # dev, uat, prod
stripe_secret                    = "whsec_..."
shopify_secret                   = "..."
github_secret                    = "..."
lambda_memory                    = 256               # MB
lambda_timeout                   = 60                # seconds
log_retention_days               = 30
enable_downstream_notifications  = false             # Enable SNS forwarding
dynamodb_billing_mode            = "PAY_PER_REQUEST"
```

## CI/CD

GitHub Actions runs on every push/PR to `main`:

- **Test** — `pytest` with mocked AWS APIs (no credentials needed)
- **Lint** — `ruff format --check` + `ruff check`
- **Terraform Validate** — `fmt -check`, `init -backend=false`, `validate`
- **Package** — Builds `router.zip`, `stripe.zip`, `shopify.zip`, `github.zip`, `generic.zip`

## Customization

**Add a new webhook source:**

1. Create `src/processors/myservice.py` with a `lambda_handler()` function
2. Add SQS queue + DLQ in `terraform/main.tf`
3. Create Lambda function and event source mapping in Terraform
4. Add route in `router.py` → `handle_myservice()` function
5. Update `requirements.txt` if needed
6. Add unit tests

**Change event retention:**

```bash
terraform plan -var="sqs_retention_seconds=604800" -out=tfplan  # 7 days
```

**Enable downstream SNS notifications:**

```bash
terraform plan -var="enable_downstream_notifications=true" -out=tfplan
```

**Increase throttling limits:**

```hcl
# In terraform/main.tf
default_route_settings {
  throttling_rate_limit  = 500    # requests/sec
  throttling_burst_limit = 1000   # burst capacity
}
```

## Example Event Flow

### Stripe Payment Intent Succeeded

**Incoming webhook:**
```json
POST /webhooks/stripe
X-Stripe-Signature: t=1234567890,v1=abc123...

{
  "id": "evt_1234",
  "type": "payment_intent.succeeded",
  "created": 1234567890,
  "data": {
    "object": {
      "id": "pi_abc123",
      "amount": 10000,
      "currency": "usd",
      "customer": "cus_xyz789",
      "status": "succeeded"
    }
  }
}
```

**Router processing:**
1. ✓ Validates Stripe signature using secret from SSM
2. ✓ Extracts and normalizes: amount ($100), customer, status
3. ✓ Routes to `stripe` SQS queue

**Processor:**
1. ✓ Receives from queue
2. ✓ Extracts key fields: `amount`, `currency`, `customer_id`, `payment_intent_id`
3. ✓ Stores in DynamoDB with TTL
4. ✓ Publishes to SNS topic (if enabled)
5. ✓ Deletes from queue

**DynamoDB entry:**
```
pk: stripe#pi_abc123
sk: event#payment_intent.succeeded
processed_data: {
  event_type: "payment_succeeded",
  amount: 100.0,
  currency: "USD",
  customer_id: "cus_xyz789",
  payment_intent_id: "pi_abc123",
  status: "succeeded",
  created: 1234567890
}
```

## Monitoring

CloudWatch dashboards track:

- **Router Lambda**: Invocation count, duration, errors
- **Processor Lambdas**: Message processing rate, DLQ depth, batch failures
- **SQS Queues**: Approximate message count, age of oldest message
- **DynamoDB**: Consumed capacity, throttling events
- **API Gateway**: Request count, latency, error rate

View alarms:

```bash
aws cloudwatch describe-alarms --alarm-name-prefix webhook-router-dev
```

Check DLQ messages (indicates failures):

```bash
aws sqs receive-message \
  --queue-url $(terraform output -raw stripe_queue_url)-dlq \
  --max-number-of-messages 10
```

## Cost Estimate

For typical small-business usage (< 1,000 webhooks/month):

| Component | Estimated Monthly Cost |
|-----------|----------------------|
| Lambda (Router + Processors) | ~$0.50 (processing time << free tier) |
| API Gateway | ~$0.35 (1k HTTP requests) |
| SQS | ~$0.10 (message throughput) |
| DynamoDB | ~$1.00 (on-demand, ~100-1000 events stored) |
| CloudWatch | ~$0.50 (logs, metrics) |
| SNS | ~$0.10 (optional notifications) |
| **Total** | **~$2.50/month** |

## Local Development

```bash
# Set up
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/
ruff format src/ tests/

# Simulate webhook locally (requires moto for AWS mocking)
python -c "
from src.router import lambda_handler
import json

event = {
    'rawPath': '/webhooks/generic',
    'body': json.dumps({'type': 'test', 'id': '123'}),
    'headers': {}
}
result = lambda_handler(event, None)
print(json.dumps(result, indent=2))
"
```

## Troubleshooting

**Signature validation fails:**
- Verify secret matches provider's webhook signing secret
- Check SSM parameter is set correctly
- Confirm body hasn't been modified

**Messages stuck in DLQ:**
- Check processor Lambda logs for parsing errors
- Verify DynamoDB has write permissions
- Check if DynamoDB table exists and is accessible

**High API latency:**
- Increase Router Lambda memory
- Check SQS queue depth (may indicate processor bottleneck)
- Scale processor Lambda concurrency

**Missing events:**
- Check CloudWatch Logs for validation failures
- Verify webhook URLs in provider settings match API Gateway endpoint
- Check DLQ for failed messages
- Inspect processor Lambda logs

## License

MIT

## Author

Charles Harvey ([linuxlsr](https://github.com/linuxlsr)) — [Three Moons Network LLC](https://threemoonsnetwork.net)
