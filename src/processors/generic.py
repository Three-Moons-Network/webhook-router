"""
Generic Event Processor

Processes custom/generic webhook events from SQS.
No signature validation; acts as a catch-all for non-standard webhooks.
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
# Event processor
# ---------------------------------------------------------------------------


def process_generic_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Process generic/custom webhook — pass through with minimal transformation."""
    return {
        "event_type": payload.get("type", "custom"),
        "data": payload,
    }


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context: Any) -> dict:
    """
    SQS handler for generic webhook events.

    Processes messages from the generic SQS queue,
    stores raw data in DynamoDB with minimal transformation.
    """
    logger.info(f"Processing {len(event.get('Records', []))} generic webhook events")

    successful = 0
    failed = 0
    batch_item_failures = []

    for record in event.get("Records", []):
        try:
            message = json.loads(record.get("body", "{}"))

            source = message.get("source", "generic")
            event_type = message.get("event_type", "custom")
            resource_id = message.get("resource_id", "unknown")
            raw_payload = message.get("raw_payload", {})

            if source != "generic":
                logger.warning(f"Expected generic source, got {source}")
                failed += 1
                batch_item_failures.append({"itemId": record.get("messageId", "")})
                continue

            # Process event
            processed = process_generic_event(raw_payload)

            # Store in DynamoDB
            table.put_item(
                Item={
                    "pk": f"generic#{resource_id}",
                    "sk": f"event#{event_type}",
                    "source": "generic",
                    "event_type": event_type,
                    "resource_id": resource_id,
                    "processed_data": processed,
                    "timestamp": message.get("timestamp", 0),
                    "ttl": int(__import__("time").time()) + (90 * 86400),
                }
            )

            logger.info(f"Processed generic event: {event_type} - {resource_id}")

            # Send downstream notification if configured
            if SNS_TOPIC_ARN:
                _send_sns_notification("generic", event_type, processed)

            successful += 1

        except Exception as exc:
            logger.error(f"Failed to process record: {exc}")
            failed += 1
            batch_item_failures.append({"itemId": record.get("messageId", "")})

    logger.info(
        f"Generic processing complete: {successful} successful, {failed} failed"
    )

    return {"batchItemFailures": batch_item_failures}


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
