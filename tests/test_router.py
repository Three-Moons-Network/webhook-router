"""
Tests for the webhook router.

Uses mocking for AWS APIs and signature validation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest

from src.router import (
    handle_github,
    handle_generic,
    handle_shopify,
    handle_stripe,
    lambda_handler,
    validate_github_signature,
    validate_shopify_signature,
    validate_stripe_signature,
)


# ---------------------------------------------------------------------------
# Signature validation
# ---------------------------------------------------------------------------

class TestSignatureValidation:
    def test_stripe_signature_valid(self):
        """Valid Stripe signature passes."""
        secret = "whsec_test"
        timestamp = "1234567890"
        body = '{"test": "data"}'
        signed_content = hmac.new(
            secret.encode(),
            f"{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        signature = f"t={timestamp},v1={signed_content}"

        assert validate_stripe_signature(body, signature, secret) is True

    def test_stripe_signature_invalid(self):
        """Invalid Stripe signature fails."""
        assert validate_stripe_signature('{"test": "data"}', "invalid", "secret") is False

    def test_shopify_signature_valid(self):
        """Valid Shopify signature passes."""
        import base64

        secret = "shopify_test"
        body = '{"test": "data"}'
        expected_sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        signature = base64.b64encode(expected_sig).decode()

        assert validate_shopify_signature(body, signature, secret) is True

    def test_shopify_signature_invalid(self):
        """Invalid Shopify signature fails."""
        assert validate_shopify_signature('{"test": "data"}', "invalid", "secret") is False

    def test_github_signature_valid(self):
        """Valid GitHub signature passes."""
        secret = "github_test"
        body = '{"test": "data"}'
        expected_hash = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        signature = f"sha256={expected_hash}"

        assert validate_github_signature(body, signature, secret) is True

    def test_github_signature_invalid(self):
        """Invalid GitHub signature fails."""
        assert validate_github_signature('{"test": "data"}', "sha256=invalid", "secret") is False


# ---------------------------------------------------------------------------
# Webhook parsing
# ---------------------------------------------------------------------------

class TestWebhookParsing:
    @patch("src.router.get_webhook_secret")
    @patch("src.router.validate_stripe_signature")
    def test_parse_stripe(self, mock_validate, mock_secret):
        """Parse Stripe webhook."""
        mock_secret.return_value = "secret"
        mock_validate.return_value = True

        body = json.dumps({
            "id": "evt_test",
            "type": "payment_intent.succeeded",
            "created": 1234567890,
            "data": {
                "object": {
                    "id": "pi_test",
                    "amount": 10000,
                    "currency": "usd",
                }
            },
        })

        result = handle_stripe(body, {"stripe-signature": "valid"})
        assert result is not None
        assert result.source == "stripe"
        assert result.event_type == "payment_intent.succeeded"

    @patch("src.router.get_webhook_secret")
    @patch("src.router.validate_shopify_signature")
    def test_parse_shopify(self, mock_validate, mock_secret):
        """Parse Shopify webhook."""
        mock_secret.return_value = "secret"
        mock_validate.return_value = True

        body = json.dumps({"id": "order_test", "total_price": "99.99"})

        result = handle_shopify(body, {
            "x-shopify-hmac-sha256": "valid",
            "x-shopify-topic": "orders/create",
            "x-shopify-shop-id": "shop123",
        })
        assert result is not None
        assert result.source == "shopify"
        assert result.event_type == "orders/create"

    @patch("src.router.get_webhook_secret")
    @patch("src.router.validate_github_signature")
    def test_parse_github(self, mock_validate, mock_secret):
        """Parse GitHub webhook."""
        mock_secret.return_value = "secret"
        mock_validate.return_value = True

        body = json.dumps({
            "action": "opened",
            "pull_request": {"id": 1},
            "repository": {"id": 123, "full_name": "test/repo"},
            "created_at": 1234567890,
        })

        result = handle_github(body, {
            "x-hub-signature-256": "valid",
            "x-github-event": "pull_request",
        })
        assert result is not None
        assert result.source == "github"
        assert result.event_type == "pull_request"

    def test_parse_generic(self):
        """Parse generic webhook (no signature validation)."""
        body = json.dumps({
            "type": "custom_event",
            "id": "evt_123",
            "timestamp": 1234567890,
        })

        result = handle_generic(body, {})
        assert result is not None
        assert result.source == "generic"
        assert result.event_type == "custom_event"


# ---------------------------------------------------------------------------
# Lambda handler integration
# ---------------------------------------------------------------------------

class TestLambdaHandler:
    @patch("src.router.route_to_queue")
    @patch("src.router.handle_stripe")
    def test_stripe_route(self, mock_parse, mock_route):
        """Stripe webhook is routed to queue."""
        mock_parse.return_value = MagicMock(source="stripe", event_type="payment_intent.succeeded")
        mock_route.return_value = True

        event = {
            "rawPath": "/webhooks/stripe",
            "body": "{}",
            "headers": {},
        }

        result = lambda_handler(event, None)
        assert result["statusCode"] == 204

    @patch("src.router.route_to_queue")
    @patch("src.router.handle_shopify")
    def test_shopify_route(self, mock_parse, mock_route):
        """Shopify webhook is routed to queue."""
        mock_parse.return_value = MagicMock(source="shopify", event_type="orders/create")
        mock_route.return_value = True

        event = {
            "rawPath": "/webhooks/shopify",
            "body": "{}",
            "headers": {},
        }

        result = lambda_handler(event, None)
        assert result["statusCode"] == 204

    def test_unknown_route_returns_404(self):
        """Unknown webhook path returns 404."""
        event = {
            "rawPath": "/webhooks/unknown",
            "body": "{}",
            "headers": {},
        }

        result = lambda_handler(event, None)
        assert result["statusCode"] == 404

    @patch("src.router.handle_stripe")
    def test_invalid_webhook_returns_400(self, mock_parse):
        """Invalid webhook signature returns 400."""
        mock_parse.return_value = None

        event = {
            "rawPath": "/webhooks/stripe",
            "body": "{}",
            "headers": {"stripe-signature": "invalid"},
        }

        result = lambda_handler(event, None)
        assert result["statusCode"] == 400

    @patch("src.router.route_to_queue")
    @patch("src.router.handle_stripe")
    def test_routing_failure_returns_500(self, mock_parse, mock_route):
        """Routing failure returns 500."""
        mock_parse.return_value = MagicMock(source="stripe")
        mock_route.return_value = False

        event = {
            "rawPath": "/webhooks/stripe",
            "body": "{}",
            "headers": {},
        }

        result = lambda_handler(event, None)
        assert result["statusCode"] == 500
