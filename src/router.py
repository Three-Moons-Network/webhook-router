"""
Webhook Router — Main entry point

Receives webhooks from external services (Stripe, Shopify, GitHub, etc.),
validates signatures, normalizes payloads, and routes to appropriate SQS queues
for reliable async processing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# AWS clients
sqs_client = boto3.client("sqs")
ssm_client = boto3.client("ssm")

# Environment
STRIPE_QUEUE_URL = os.environ.get("STRIPE_QUEUE_URL", "")
SHOPIFY_QUEUE_URL = os.environ.get("SHOPIFY_QUEUE_URL", "")
GITHUB_QUEUE_URL = os.environ.get("GITHUB_QUEUE_URL", "")
GENERIC_QUEUE_URL = os.environ.get("GENERIC_QUEUE_URL", "")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class NormalizedEvent:
    """Normalized webhook event across all sources."""

    source: str
    event_type: str
    timestamp: int
    resource_id: str
    payload: dict[str, Any]
    raw_payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Signature validation
# ---------------------------------------------------------------------------


def validate_stripe_signature(body: str, signature: str, secret: str) -> bool:
    """Validate Stripe webhook signature."""
    try:
        timestamp, signed_content = signature.split(",")
        timestamp = timestamp.replace("t=", "")
        signed_content = signed_content.replace("v1=", "")

        expected_sig = hmac.new(
            secret.encode(),
            f"{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signed_content, expected_sig)
    except Exception as exc:
        logger.warning(f"Stripe signature validation failed: {exc}")
        return False


def validate_shopify_signature(body: str, signature: str, secret: str) -> bool:
    """Validate Shopify webhook signature."""
    try:
        expected_sig = hmac.new(
            secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).digest()

        import base64

        received_sig = base64.b64decode(signature)

        return hmac.compare_digest(expected_sig, received_sig)
    except Exception as exc:
        logger.warning(f"Shopify signature validation failed: {exc}")
        return False


def validate_github_signature(body: str, signature: str, secret: str) -> bool:
    """Validate GitHub webhook signature."""
    try:
        algo, received_hash = signature.split("=")
        expected_hash = hmac.new(
            secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected_hash, received_hash)
    except Exception as exc:
        logger.warning(f"GitHub signature validation failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Get secrets from SSM
# ---------------------------------------------------------------------------


def get_webhook_secret(source: str) -> str:
    """Retrieve webhook secret from SSM Parameter Store."""
    try:
        param_name = f"/webhook-router/secrets/{source}"
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except Exception as exc:
        logger.warning(f"Failed to retrieve {source} secret: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Webhook route handlers
# ---------------------------------------------------------------------------


def handle_stripe(body: str, headers: dict[str, str]) -> NormalizedEvent | None:
    """Parse and validate Stripe webhook."""
    signature = headers.get("stripe-signature", "")
    secret = get_webhook_secret("stripe")

    if not secret or not validate_stripe_signature(body, signature, secret):
        logger.warning("Stripe signature validation failed")
        return None

    try:
        payload = json.loads(body)
        event_type = payload.get("type", "unknown")
        data = payload.get("data", {}).get("object", {})

        return NormalizedEvent(
            source="stripe",
            event_type=event_type,
            timestamp=payload.get("created", 0),
            resource_id=data.get("id", ""),
            payload={
                "event_id": payload.get("id"),
                "event_type": event_type,
                "data": data,
            },
            raw_payload=payload,
        )
    except Exception as exc:
        logger.error(f"Failed to parse Stripe webhook: {exc}")
        return None


def handle_shopify(body: str, headers: dict[str, str]) -> NormalizedEvent | None:
    """Parse and validate Shopify webhook."""
    signature = headers.get("x-shopify-hmac-sha256", "")
    secret = get_webhook_secret("shopify")

    if not secret or not validate_shopify_signature(body, signature, secret):
        logger.warning("Shopify signature validation failed")
        return None

    try:
        payload = json.loads(body)
        event_type = headers.get("x-shopify-topic", "unknown")
        resource_id = headers.get("x-shopify-shop-id", "")

        return NormalizedEvent(
            source="shopify",
            event_type=event_type,
            timestamp=int(
                headers.get("x-shopify-api-call-limit", "0").split("/")[1] or 0
            ),
            resource_id=resource_id,
            payload={
                "event_type": event_type,
                "data": payload,
            },
            raw_payload=payload,
        )
    except Exception as exc:
        logger.error(f"Failed to parse Shopify webhook: {exc}")
        return None


def handle_github(body: str, headers: dict[str, str]) -> NormalizedEvent | None:
    """Parse and validate GitHub webhook."""
    signature = headers.get("x-hub-signature-256", "")
    secret = get_webhook_secret("github")

    if not secret or not validate_github_signature(body, signature, secret):
        logger.warning("GitHub signature validation failed")
        return None

    try:
        payload = json.loads(body)
        event_type = headers.get("x-github-event", "unknown")
        repo = payload.get("repository", {})
        resource_id = repo.get("id", "")

        return NormalizedEvent(
            source="github",
            event_type=event_type,
            timestamp=int(payload.get("created_at", 0))
            if payload.get("created_at")
            else 0,
            resource_id=str(resource_id),
            payload={
                "event_type": event_type,
                "repository": repo.get("full_name", ""),
                "data": payload,
            },
            raw_payload=payload,
        )
    except Exception as exc:
        logger.error(f"Failed to parse GitHub webhook: {exc}")
        return None


def handle_generic(body: str, headers: dict[str, str]) -> NormalizedEvent | None:
    """Parse generic/custom webhook (no signature validation)."""
    try:
        payload = json.loads(body)
        event_type = headers.get("x-webhook-type", payload.get("type", "custom"))
        resource_id = payload.get("id", "unknown")

        return NormalizedEvent(
            source="generic",
            event_type=event_type,
            timestamp=payload.get("timestamp", 0),
            resource_id=resource_id,
            payload=payload,
            raw_payload=payload,
        )
    except Exception as exc:
        logger.error(f"Failed to parse generic webhook: {exc}")
        return None


# ---------------------------------------------------------------------------
# SQS routing
# ---------------------------------------------------------------------------


def route_to_queue(event: NormalizedEvent) -> bool:
    """Route normalized event to appropriate SQS queue."""
    queue_mapping = {
        "stripe": STRIPE_QUEUE_URL,
        "shopify": SHOPIFY_QUEUE_URL,
        "github": GITHUB_QUEUE_URL,
        "generic": GENERIC_QUEUE_URL,
    }

    queue_url = queue_mapping.get(event.source)
    if not queue_url:
        logger.error(f"No queue configured for source: {event.source}")
        return False

    try:
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(asdict(event)),
            MessageAttributes={
                "source": {"StringValue": event.source, "DataType": "String"},
                "event_type": {"StringValue": event.event_type, "DataType": "String"},
                "resource_id": {"StringValue": event.resource_id, "DataType": "String"},
            },
        )
        logger.info(
            f"Routed {event.source}/{event.event_type} to queue: {event.resource_id}"
        )
        return True
    except Exception as exc:
        logger.error(f"Failed to send message to SQS: {exc}")
        return False


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context: Any) -> dict:
    """
    API Gateway handler for webhook router.

    Routes:
      - POST /webhooks/stripe   → Stripe events
      - POST /webhooks/shopify  → Shopify events
      - POST /webhooks/github   → GitHub events
      - POST /webhooks/generic  → Custom webhooks

    Returns 204 on success (webhook has been queued for processing).
    Returns 400 on validation failure.
    Returns 500 on unexpected errors.
    """
    logger.info(f"Received webhook: {event.get('rawPath', 'unknown')}")

    try:
        # Parse request
        body = event.get("body", "")
        headers = event.get("headers", {})
        path = event.get("rawPath", "").lower()

        # Determine source and parse
        normalized_event = None

        if "/webhooks/stripe" in path:
            normalized_event = handle_stripe(body, headers)
        elif "/webhooks/shopify" in path:
            normalized_event = handle_shopify(body, headers)
        elif "/webhooks/github" in path:
            normalized_event = handle_github(body, headers)
        elif "/webhooks/generic" in path:
            normalized_event = handle_generic(body, headers)
        else:
            logger.warning(f"Unknown webhook path: {path}")
            return {
                "statusCode": 404,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Webhook endpoint not found"}),
            }

        if not normalized_event:
            logger.warning("Failed to parse webhook")
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Invalid webhook or signature"}),
            }

        # Route to SQS
        if not route_to_queue(normalized_event):
            logger.error("Failed to queue webhook")
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Failed to process webhook"}),
            }

        # Return 204 (webhook accepted for processing)
        return {
            "statusCode": 204,
            "headers": {"Content-Type": "application/json"},
        }

    except Exception:
        logger.exception("Unexpected error in webhook router")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }
