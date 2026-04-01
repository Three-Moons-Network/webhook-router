"""
Shopify Event Processor

Processes Shopify webhook events from SQS:
  - orders/create: new order placed
  - orders/fulfilled: order shipped
  - products/update: product info changed
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


def process_order_created(payload: dict[str, Any]) -> dict[str, Any]:
    """Process new Shopify order."""
    order_number = payload.get("order_number", 0)
    total_price = float(payload.get("total_price", 0))
    currency = payload.get("currency", "USD")
    customer = payload.get("customer", {})
    line_items = payload.get("line_items", [])

    return {
        "event_type": "order_created",
        "order_number": order_number,
        "order_id": payload.get("id", ""),
        "customer_email": customer.get("email", ""),
        "customer_name": f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip(),
        "total_price": total_price,
        "currency": currency,
        "item_count": len(line_items),
        "created_at": payload.get("created_at", ""),
    }


def process_order_fulfilled(payload: dict[str, Any]) -> dict[str, Any]:
    """Process Shopify order fulfillment."""
    return {
        "event_type": "order_fulfilled",
        "order_id": payload.get("id", ""),
        "order_number": payload.get("order_number", 0),
        "fulfillment_status": payload.get("fulfillment_status", ""),
        "updated_at": payload.get("updated_at", ""),
    }


def process_product_updated(payload: dict[str, Any]) -> dict[str, Any]:
    """Process Shopify product update."""
    return {
        "event_type": "product_updated",
        "product_id": payload.get("id", ""),
        "title": payload.get("title", ""),
        "status": payload.get("status", ""),
        "variants_count": len(payload.get("variants", [])),
        "updated_at": payload.get("updated_at", ""),
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

EVENT_HANDLERS = {
    "orders/create": process_order_created,
    "orders/fulfilled": process_order_fulfilled,
    "products/update": process_product_updated,
}


def process_shopify_event(message: dict[str, Any]) -> bool:
    """Process a single Shopify webhook event from SQS."""
    try:
        source = message.get("source", "")
        event_type = message.get("event_type", "")
        resource_id = message.get("resource_id", "")
        raw_payload = message.get("raw_payload", {})

        if source != "shopify":
            logger.warning(f"Expected shopify source, got {source}")
            return False

        # Get handler for this event type
        handler = EVENT_HANDLERS.get(event_type)
        if not handler:
            logger.warning(f"No handler for Shopify event type: {event_type}")
            return True  # Skip unknown events but don't fail

        # Process event
        processed = handler(raw_payload)

        # Store in DynamoDB
        table.put_item(
            Item={
                "pk": f"shopify#{resource_id}",
                "sk": f"event#{event_type}",
                "source": "shopify",
                "event_type": event_type,
                "resource_id": resource_id,
                "processed_data": processed,
                "timestamp": message.get("timestamp", 0),
                "ttl": int(__import__("time").time()) + (90 * 86400),
            }
        )

        logger.info(f"Processed Shopify event: {event_type} - {resource_id}")

        # Send downstream notification if configured
        if SNS_TOPIC_ARN and event_type in ["orders/create", "orders/fulfilled"]:
            _send_sns_notification("shopify", event_type, processed)

        return True

    except Exception as exc:
        logger.error(f"Error processing Shopify event: {exc}")
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
    SQS handler for Shopify webhook events.

    Processes messages from the Shopify SQS queue,
    normalizes the data, and stores in DynamoDB.
    """
    logger.info(f"Processing {len(event.get('Records', []))} Shopify webhook events")

    successful = 0
    failed = 0
    batch_item_failures = []

    for record in event.get("Records", []):
        try:
            message = json.loads(record.get("body", "{}"))
            if process_shopify_event(message):
                successful += 1
            else:
                failed += 1
                batch_item_failures.append({"itemId": record.get("messageId", "")})
        except Exception as exc:
            logger.error(f"Failed to process record: {exc}")
            failed += 1
            batch_item_failures.append({"itemId": record.get("messageId", "")})

    logger.info(
        f"Shopify processing complete: {successful} successful, {failed} failed"
    )

    return {"batchItemFailures": batch_item_failures}
