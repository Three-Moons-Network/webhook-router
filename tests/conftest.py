"""Shared test configuration for webhook-router tests."""

import os
import sys
from unittest.mock import MagicMock

import pytest

# Add repo root to Python path so imports work correctly
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Set AWS credentials and region BEFORE any boto3 imports
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

# Mock boto3.client before the module imports it
import boto3

_original_client = boto3.client
_mock_clients = {}


def _mock_boto3_client(service_name, *args, **kwargs):
    """Factory function to return mocked clients."""
    if service_name not in _mock_clients:
        _mock_clients[service_name] = MagicMock()
    return _mock_clients[service_name]


boto3.client = _mock_boto3_client


@pytest.fixture
def mock_sqs():
    """Get the mocked SQS client."""
    return _mock_clients.get("sqs", MagicMock())


@pytest.fixture
def mock_ssm():
    """Get the mocked SSM client."""
    return _mock_clients.get("ssm", MagicMock())
