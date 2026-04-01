"""
GitHub Event Processor

Processes GitHub webhook events from SQS:
  - push: commits pushed to repo
  - pull_request: PR opened, closed, or merged
  - issues: issue opened, closed, commented
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

def process_push(payload: dict[str, Any]) -> dict[str, Any]:
    """Process GitHub push event."""
    repo = payload.get("repository", {})
    pusher = payload.get("pusher", {})
    commits = payload.get("commits", [])

    return {
        "event_type": "push",
        "repository": repo.get("full_name", ""),
        "branch": payload.get("ref", "").split("/")[-1],
        "pusher": pusher.get("name", ""),
        "commit_count": len(commits),
        "commits": [
            {
                "sha": c.get("id", "")[:8],
                "message": c.get("message", "").split("\n")[0],
                "author": c.get("author", {}).get("name", ""),
            }
            for c in commits[:5]
        ],
        "timestamp": payload.get("created_at", ""),
    }


def process_pull_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Process GitHub pull request event."""
    repo = payload.get("repository", {})
    pr = payload.get("pull_request", {})
    action = payload.get("action", "")

    return {
        "event_type": "pull_request",
        "action": action,
        "repository": repo.get("full_name", ""),
        "pr_number": pr.get("number", 0),
        "pr_title": pr.get("title", ""),
        "author": pr.get("user", {}).get("login", ""),
        "target_branch": pr.get("base", {}).get("ref", ""),
        "source_branch": pr.get("head", {}).get("ref", ""),
        "timestamp": payload.get("created_at", ""),
    }


def process_issues(payload: dict[str, Any]) -> dict[str, Any]:
    """Process GitHub issues event."""
    repo = payload.get("repository", {})
    issue = payload.get("issue", {})
    action = payload.get("action", "")

    return {
        "event_type": "issues",
        "action": action,
        "repository": repo.get("full_name", ""),
        "issue_number": issue.get("number", 0),
        "issue_title": issue.get("title", ""),
        "author": issue.get("user", {}).get("login", ""),
        "labels": [l.get("name", "") for l in issue.get("labels", [])],
        "timestamp": payload.get("created_at", ""),
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

EVENT_HANDLERS = {
    "push": process_push,
    "pull_request": process_pull_request,
    "issues": process_issues,
}


def process_github_event(message: dict[str, Any]) -> bool:
    """Process a single GitHub webhook event from SQS."""
    try:
        source = message.get("source", "")
        event_type = message.get("event_type", "")
        resource_id = message.get("resource_id", "")
        raw_payload = message.get("raw_payload", {})

        if source != "github":
            logger.warning(f"Expected github source, got {source}")
            return False

        # Get handler for this event type
        handler = EVENT_HANDLERS.get(event_type)
        if not handler:
            logger.warning(f"No handler for GitHub event type: {event_type}")
            return True  # Skip unknown events but don't fail

        # Process event
        processed = handler(raw_payload)

        # Store in DynamoDB
        table.put_item(
            Item={
                "pk": f"github#{resource_id}",
                "sk": f"event#{event_type}",
                "source": "github",
                "event_type": event_type,
                "resource_id": resource_id,
                "processed_data": processed,
                "timestamp": message.get("timestamp", 0),
                "ttl": int(__import__("time").time()) + (90 * 86400),
            }
        )

        logger.info(f"Processed GitHub event: {event_type} - {resource_id}")

        # Send downstream notification if configured
        if SNS_TOPIC_ARN and event_type in ["push", "pull_request"]:
            _send_sns_notification("github", event_type, processed)

        return True

    except Exception as exc:
        logger.error(f"Error processing GitHub event: {exc}")
        return False


def _send_sns_notification(source: str, event_type: str, processed_data: dict[str, Any]) -> None:
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
    SQS handler for GitHub webhook events.

    Processes messages from the GitHub SQS queue,
    normalizes the data, and stores in DynamoDB.
    """
    logger.info(f"Processing {len(event.get('Records', []))} GitHub webhook events")

    successful = 0
    failed = 0
    batch_item_failures = []

    for record in event.get("Records", []):
        try:
            message = json.loads(record.get("body", "{}"))
            if process_github_event(message):
                successful += 1
            else:
                failed += 1
                batch_item_failures.append({"itemId": record.get("messageId", "")})
        except Exception as exc:
            logger.error(f"Failed to process record: {exc}")
            failed += 1
            batch_item_failures.append({"itemId": record.get("messageId", "")})

    logger.info(f"GitHub processing complete: {successful} successful, {failed} failed")

    return {"batchItemFailures": batch_item_failures}
