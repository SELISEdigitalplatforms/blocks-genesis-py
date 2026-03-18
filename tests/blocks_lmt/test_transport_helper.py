import pytest

from blocks_lmt.transport_helper import is_rabbitmq


class TestIsRabbitMq:
    """Test cases for transport auto-detection."""

    def test_amqp_scheme_returns_true(self):
        assert is_rabbitmq("amqp://guest:guest@localhost:5672/") is True

    def test_amqps_scheme_returns_true(self):
        assert is_rabbitmq("amqps://user:pass@rabbitmq.example.com:5671/vhost") is True

    def test_azure_servicebus_connection_string_returns_false(self):
        conn = "Endpoint=sb://test.servicebus.windows.net/;SharedAccessKeyName=test;SharedAccessKey=testkey"
        assert is_rabbitmq(conn) is False

    def test_empty_string_returns_false(self):
        assert is_rabbitmq("") is False

    def test_none_returns_false(self):
        assert is_rabbitmq(None) is False

    def test_whitespace_returns_false(self):
        assert is_rabbitmq("   ") is False

    def test_invalid_uri_returns_false(self):
        assert is_rabbitmq("not-a-valid-uri") is False

    def test_http_scheme_returns_false(self):
        assert is_rabbitmq("http://localhost:15672") is False

    def test_amqp_uppercase_returns_true(self):
        assert is_rabbitmq("AMQP://guest:guest@localhost:5672/") is True

    def test_amqps_mixed_case_returns_true(self):
        assert is_rabbitmq("AmQpS://user:pass@host:5671/") is True
