"""Property tests for _get_provider (I04-WP5, Python part).

The rule under test: an amqp or amqps connection string means RabbitMQ, and any other string means Azure.
A failing property has found a bug. Keep the failing example and report it; never weaken the property.
"""

from urllib.parse import urlparse

from hypothesis import given, settings
from hypothesis import strategies as st

from blocks_genesis._message.message_configuration import _get_provider


def _scheme(text: str) -> str:
    """The scheme urlparse reads, or "" when urlparse itself rejects the text."""
    try:
        return urlparse(text).scheme.lower()
    except ValueError:
        return ""


@settings(max_examples=300, deadline=None)
@given(
    scheme=st.sampled_from(["amqp", "amqps", "AMQP", "Amqps"]),
    rest=st.text(),
)
def test_every_amqp_or_amqps_string_is_rabbitmq(scheme, rest):
    assert _get_provider(f"{scheme}://{rest}") == "rabbitmq"


@settings(max_examples=300, deadline=None)
@given(text=st.text().filter(lambda s: _scheme(s) not in ("amqp", "amqps")))
def test_every_other_string_is_azure(text):
    assert _get_provider(text) == "azure"
