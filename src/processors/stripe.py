"""
Stripe Event Processor

Processes Stripe webhook events from SQS:
  - payment_intent.succeeded: payment completed
  - invoice.paid: invoice payment processed
  - charge.failed: charge failed
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# AWS clients
sns_client = boto3.client("sns")
dynamodb = boto3.resource("dynamodb")

# Environment
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "webhook-events")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")

table = dynamodb.Table(DYNAMODB_TABLE)


# ---------------------------------------------------------------------------
# Event processors
# ---------------------------------------------------------------------------


def process_payment_intent_succeeded(payload: dict[str, Any]) -> dict[str, Any]:
    """Process successful Stripe payment intent."""
    data = payload.get("data", {})
    amount = int(data.get("amount", 0)) / 100  # Convert cents to dollars
    currency = data.get("currency", "usd").upper()
    customer_id = data.get("customer", "")

    return {
        "event_type": "payment_succeeded",
        "amount": amount,
        "currency": currency,
        "customer_id": customer_id,
        "payment_intent_id": data.get("id", ""),
        "status": data.get("status", ""),
        "created": data.get("created", 0),
    }


def process_invoice_paid(payload: dict[str, Any]) -> dict[str, Any]:
    """Process Stripe invoice payment."""
    data = payload.get("data", {})
    amount = int(data.get("amount_paid", 0)) / 100
    currency = data.get("currency", "usd").upper()
    customer_id = data.get("customer", "")

    return {
        "event_type": "invoice_paid",
        "amount": amount,
        "currency": currency,
        "customer_id": customer_id,
        "invoice_id": data.get("id", ""),
        "status": data.get("status", ""),
        "created": data.get("created", 0),
    }


def process_charge_failed(payload: dict[str, Any]) -> dict[str, Any]:
    """Process failed Stripe charge."""
    data = payload.get("data", {})
    amount = int(data.get("amount", 0)) / 100
    currency = data.get("currency", "usd").upper()
    customer_id = data.get("customer", "")

    return {
        "event_type": "charge_failed",
        "amount": amount,
        "currency": currency,
        "customer_id": customer_id,
        "charge_id": data.get("id", ""),
        "failure_reason": data.get("failure_message", ""),
        "created": data.get("created", 0),
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

EVENT_HANDLERS = {
    "payment_intent.succeeded": process_payment_intent_succeeded,
    "invoice.paid": process_invoice_paid,
    "charge.failed": process_charge_failed,
}


def process_stripe_event(message: dict[str, Any]) -> bool:
    """Process a single Stripe webhook event from SQS."""
    try:
        source = message.get("source", "")
        event_type = message.get("event_type", "")
        resource_id = message.get("resource_id", "")
        raw_payload = message.get("raw_payload", {})

        if source != "stripe":
            logger.warning(f"Expected stripe source, got {source}")
            return False

        # Get handler for this event type
        handler = EVENT_HANDLERS.get(event_type)
        if not handler:
            logger.warning(f"No handler for Stripe event type: {event_type}")
            return True  # Skip unknown events but don't fail

        # Process event
        processed = handler(raw_payload)

        # Store in DynamoDB
        table.put_item(
            Item={
                "pk": f"stripe#{resource_id}",
                "sk": f"event#{event_type}",
                "source": "stripe",
                "event_type": event_type,
                "resource_id": resource_id,
                "processed_data": processed,
                "timestamp": message.get("timestamp", 0),
                "ttl": int(__import__("time").time())
                + (90 * 86400),  # 90 day retention
            }
        )

        logger.info(f"Processed Stripe event: {event_type} - {resource_id}")

        # Send downstream notification if configured
        if SNS_TOPIC_ARN and event_type in [
            "payment_intent.succeeded",
            "charge.failed",
        ]:
            _send_sns_notification("stripe", event_type, processed)

        return True

    except Exception as exc:
        logger.error(f"Error processing Stripe event: {exc}")
        return False


def _send_sns_notification(
    source: str, event_type: str, processed_data: dict[str, Any]
) -> None:
    """Send processed event to SNS for downstream processing."""
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"Webhook: {source}/{event_type}",
            Message=json.dumps(processed_data),
        )
    except Exception as exc:
        logger.error(f"Failed to send SNS notification: {exc}")


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context: Any) -> dict:
    """
    SQS handler for Stripe webhook events.

    Processes messages from the Stripe SQS queue,
    normalizes the data, and stores in DynamoDB.
    """
    logger.info(f"Processing {len(event.get('Records', []))} Stripe webhook events")

    successful = 0
    failed = 0
    batch_item_failures = []

    for record in event.get("Records", []):
        try:
            message = json.loads(record.get("body", "{}"))
            if process_stripe_event(message):
                successful += 1
            else:
                failed += 1
                batch_item_failures.append({"itemId": record.get("messageId", "")})
        except Exception as exc:
            logger.error(f"Failed to process record: {exc}")
            failed += 1
            batch_item_failures.append({"itemId": record.get("messageId", "")})

    logger.info(f"Stripe processing complete: {successful} successful, {failed} failed")

    return {"batchItemFailures": batch_item_failures}
