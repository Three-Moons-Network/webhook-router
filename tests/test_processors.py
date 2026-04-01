"""
Tests for webhook event processors.

Uses mocking for DynamoDB and SNS.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stripe processor tests
# ---------------------------------------------------------------------------

class TestStripeProcessor:
    @patch("src.processors.stripe.table")
    @patch("src.processors.stripe.sns_client")
    def test_process_payment_succeeded(self, mock_sns, mock_table):
        """Process Stripe payment_intent.succeeded event."""
        from src.processors.stripe import process_stripe_event

        message = {
            "source": "stripe",
            "event_type": "payment_intent.succeeded",
            "resource_id": "pi_123",
            "timestamp": 1234567890,
            "raw_payload": {
                "data": {
                    "object": {
                        "id": "pi_123",
                        "amount": 10000,
                        "currency": "usd",
                        "customer": "cus_123",
                        "status": "succeeded",
                        "created": 1234567890,
                    }
                }
            },
        }

        result = process_stripe_event(message)
        assert result is True
        assert mock_table.put_item.called

    @patch("src.processors.stripe.table")
    def test_stripe_unknown_event_skipped(self, mock_table):
        """Unknown Stripe event type is skipped."""
        from src.processors.stripe import process_stripe_event

        message = {
            "source": "stripe",
            "event_type": "customer.updated",
            "resource_id": "cus_123",
            "raw_payload": {},
        }

        result = process_stripe_event(message)
        assert result is True  # Skipped but not failed


# ---------------------------------------------------------------------------
# Shopify processor tests
# ---------------------------------------------------------------------------

class TestShopifyProcessor:
    @patch("src.processors.shopify.table")
    @patch("src.processors.shopify.sns_client")
    def test_process_order_created(self, mock_sns, mock_table):
        """Process Shopify orders/create event."""
        from src.processors.shopify import process_shopify_event

        message = {
            "source": "shopify",
            "event_type": "orders/create",
            "resource_id": "shop123",
            "timestamp": 1234567890,
            "raw_payload": {
                "id": "order_123",
                "order_number": 1001,
                "total_price": "99.99",
                "currency": "USD",
                "customer": {
                    "email": "user@example.com",
                    "first_name": "John",
                    "last_name": "Doe",
                },
                "line_items": [{"id": 1}, {"id": 2}],
            },
        }

        result = process_shopify_event(message)
        assert result is True
        assert mock_table.put_item.called

    @patch("src.processors.shopify.table")
    def test_shopify_unknown_event_skipped(self, mock_table):
        """Unknown Shopify event type is skipped."""
        from src.processors.shopify import process_shopify_event

        message = {
            "source": "shopify",
            "event_type": "inventory/update",
            "resource_id": "shop123",
            "raw_payload": {},
        }

        result = process_shopify_event(message)
        assert result is True


# ---------------------------------------------------------------------------
# GitHub processor tests
# ---------------------------------------------------------------------------

class TestGitHubProcessor:
    @patch("src.processors.github.table")
    @patch("src.processors.github.sns_client")
    def test_process_push(self, mock_sns, mock_table):
        """Process GitHub push event."""
        from src.processors.github import process_github_event

        message = {
            "source": "github",
            "event_type": "push",
            "resource_id": "123",
            "timestamp": 1234567890,
            "raw_payload": {
                "repository": {"full_name": "test/repo"},
                "ref": "refs/heads/main",
                "pusher": {"name": "alice"},
                "commits": [
                    {"id": "abc123", "message": "Fix bug", "author": {"name": "alice"}},
                ],
            },
        }

        result = process_github_event(message)
        assert result is True
        assert mock_table.put_item.called

    @patch("src.processors.github.table")
    @patch("src.processors.github.sns_client")
    def test_process_pull_request(self, mock_sns, mock_table):
        """Process GitHub pull_request event."""
        from src.processors.github import process_github_event

        message = {
            "source": "github",
            "event_type": "pull_request",
            "resource_id": "123",
            "timestamp": 1234567890,
            "raw_payload": {
                "action": "opened",
                "repository": {"full_name": "test/repo"},
                "pull_request": {
                    "number": 42,
                    "title": "Add feature",
                    "user": {"login": "alice"},
                    "base": {"ref": "main"},
                    "head": {"ref": "feature/new"},
                },
            },
        }

        result = process_github_event(message)
        assert result is True

    @patch("src.processors.github.table")
    def test_github_unknown_event_skipped(self, mock_table):
        """Unknown GitHub event type is skipped."""
        from src.processors.github import process_github_event

        message = {
            "source": "github",
            "event_type": "release",
            "resource_id": "123",
            "raw_payload": {},
        }

        result = process_github_event(message)
        assert result is True


# ---------------------------------------------------------------------------
# Generic processor tests
# ---------------------------------------------------------------------------

class TestGenericProcessor:
    @patch("src.processors.generic.table")
    @patch("src.processors.generic.sns_client")
    def test_process_generic_event(self, mock_sns, mock_table):
        """Process generic custom webhook."""
        from src.processors.generic import lambda_handler

        event = {
            "Records": [
                {
                    "messageId": "msg_123",
                    "body": json.dumps({
                        "source": "generic",
                        "event_type": "custom",
                        "resource_id": "res_123",
                        "raw_payload": {"custom_field": "value"},
                    }),
                }
            ]
        }

        result = lambda_handler(event, None)
        assert result["batchItemFailures"] == []
        assert mock_table.put_item.called


# ---------------------------------------------------------------------------
# Lambda handler batch processing
# ---------------------------------------------------------------------------

class TestBatchProcessing:
    @patch("src.processors.stripe.table")
    def test_stripe_batch_processing(self, mock_table):
        """Process batch of Stripe events."""
        from src.processors.stripe import lambda_handler

        event = {
            "Records": [
                {
                    "messageId": "msg_1",
                    "body": json.dumps({
                        "source": "stripe",
                        "event_type": "payment_intent.succeeded",
                        "resource_id": "pi_1",
                        "raw_payload": {
                            "data": {
                                "object": {
                                    "id": "pi_1",
                                    "amount": 1000,
                                    "currency": "usd",
                                }
                            }
                        },
                    }),
                },
                {
                    "messageId": "msg_2",
                    "body": json.dumps({
                        "source": "stripe",
                        "event_type": "charge.failed",
                        "resource_id": "ch_2",
                        "raw_payload": {
                            "data": {
                                "object": {
                                    "id": "ch_2",
                                    "amount": 500,
                                    "failure_message": "Card declined",
                                }
                            }
                        },
                    }),
                },
            ]
        }

        result = lambda_handler(event, None)
        assert result["batchItemFailures"] == []
        assert mock_table.put_item.call_count >= 2

    @patch("src.processors.stripe.table")
    def test_batch_with_failures(self, mock_table):
        """Batch processing with some failures."""
        from src.processors.stripe import lambda_handler

        mock_table.put_item.side_effect = [None, Exception("DynamoDB error")]

        event = {
            "Records": [
                {
                    "messageId": "msg_1",
                    "body": json.dumps({
                        "source": "stripe",
                        "event_type": "payment_intent.succeeded",
                        "resource_id": "pi_1",
                        "raw_payload": {"data": {"object": {"id": "pi_1"}}},
                    }),
                },
                {
                    "messageId": "msg_2",
                    "body": json.dumps({
                        "source": "stripe",
                        "event_type": "charge.failed",
                        "resource_id": "ch_2",
                        "raw_payload": {"data": {"object": {"id": "ch_2"}}},
                    }),
                },
            ]
        }

        result = lambda_handler(event, None)
        # One failure should be reported
        assert len(result["batchItemFailures"]) >= 1
