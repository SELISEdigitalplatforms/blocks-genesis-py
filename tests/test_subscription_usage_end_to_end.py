"""End-to-end: a real FastAPI request through the real dependency, service and repository.

Only auth and the Mongo collection are substituted. Shows what mocked units cannot: that a
ContextVar set inside a dependency reaches the route handler.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from bson import Decimal128
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._subscription.context import SubscriptionUsageContext

AUTH = "blocks_genesis._auth.auth."
REPO = "blocks_genesis._subscription.repository."


class _FakeCollection:
    """Applies the filter the repository actually builds, so the query shape is exercised."""

    def __init__(self, docs):
        self.docs = docs
        self.last_filter = None

    def find(self, filt):
        self.last_filter = filt
        return [d for d in self.docs if self._matches(d, filt)]

    @staticmethod
    def _matches(doc, filt):
        for key, value in filt.items():
            actual = doc.get(key)
            if isinstance(value, dict):
                if "$lte" in value and not (actual is not None and actual <= value["$lte"]):
                    return False
                if "$gt" in value and not (actual is not None and actual > value["$gt"]):
                    return False
            elif actual != value:
                return False
        return True


class _FakeProvider:
    def __init__(self, collection):
        self.collection = collection
        self.requested = []

    async def get_collection(self, name, tenant_id=None):
        self.requested.append((name, tenant_id))
        return self.collection


def _row(meter_key, used, included, remaining, overage, *, status=2, org="default", tenant="t1"):
    now = datetime.now(timezone.utc)
    return {
        "_id": f"sub-1:{meter_key}:M20260902T024500Z",
        "TenantId": tenant,
        "OrganizationId": org,
        "SubscriptionStatus": status,
        "MeterKey": meter_key,
        "PeriodStartUtc": now - timedelta(days=1),
        "PeriodEndUtc": now + timedelta(days=29),
        "Included": Decimal128(included),
        "Used": Decimal128(used),
        "Remaining": Decimal128(remaining),
        "Overage": Decimal128(overage),
        "OverageAllowed": True,
    }


def _build_app(docs, *, tenant_id="t1", organization_id="default"):
    """A route whose handler reads only SubscriptionUsageContext -- never a passed argument."""
    from blocks_genesis._auth.auth import subscription_usage_snapshot

    collection = _FakeCollection(docs)
    provider = _FakeProvider(collection)
    context = BlocksContext(
        tenant_id=tenant_id, organization_id=organization_id, is_authenticated=True,
    )

    app = FastAPI()

    @app.get("/usage", dependencies=[subscription_usage_snapshot(bypass_authorization=False)])
    async def read_usage():
        snapshot = SubscriptionUsageContext.current()
        if snapshot is None:
            return {"snapshot": None}
        return {
            "snapshot": [
                {"meter_key": r.meter_key, "used": r.used, "remaining": r.remaining,
                 "overage": r.overage, "allowed": r.allowed}
                for r in snapshot
            ]
        }

    return app, provider, collection, context


@pytest.fixture(autouse=True)
def _clear_context():
    SubscriptionUsageContext.clear()
    yield
    SubscriptionUsageContext.clear()


def _call(app, context):
    """Drive one request with the BlocksContext the dependency expects already in place."""
    with patch(AUTH + "BlocksContextManager") as mock_mgr, patch(REPO + "DbContext") as mock_db:
        mock_mgr.get_context.return_value = context
        mock_db.get_provider.return_value = _CURRENT_PROVIDER[0]
        with TestClient(app) as client:
            return client.get("/usage")


_CURRENT_PROVIDER = [None]


def test_a_real_request_carries_the_snapshot_from_dependency_to_handler():
    app, provider, collection, context = _build_app([
        _row("ai-credits", used="7.5", included="550.55", remaining="543.05", overage="0"),
    ])
    _CURRENT_PROVIDER[0] = provider

    response = _call(app, context)

    assert response.status_code == 200, response.text
    body = response.json()
    # The handler received the snapshot purely through the ambient context.
    assert body["snapshot"] == [{
        "meter_key": "ai-credits", "used": 7.5, "remaining": 543.05,
        "overage": 0.0, "allowed": True,
    }]
    # ...and it came from the collection the repository asked for, scoped to the tenant.
    assert provider.requested == [("SubscriptionUsageCurrent", "t1")]
    assert collection.last_filter["TenantId"] == "t1"
    assert collection.last_filter["OrganizationId"] == "default"
    assert collection.last_filter["SubscriptionStatus"] == 2


def test_a_real_request_with_no_matching_row_reports_an_empty_snapshot():
    # A different org's row must not leak into this caller's snapshot.
    app, provider, _, context = _build_app([
        _row("ai-credits", used="1", included="10", remaining="9", overage="0", org="other-org"),
    ])
    _CURRENT_PROVIDER[0] = provider

    body = _call(app, context).json()

    assert body["snapshot"] == []


def test_a_real_request_survives_a_db_failure_without_failing_the_route():
    app, provider, collection, context = _build_app([])
    _CURRENT_PROVIDER[0] = provider
    collection.find = MagicMock(side_effect=ConnectionError("mongo down"))

    response = _call(app, context)

    # Fail open: the route still answers, with the snapshot unknown rather than an error.
    assert response.status_code == 200, response.text
    assert response.json()["snapshot"] is None


def test_the_snapshot_does_not_leak_between_two_requests():
    app, provider, collection, context = _build_app([
        _row("ai-credits", used="7.5", included="550.55", remaining="543.05", overage="0"),
    ])
    _CURRENT_PROVIDER[0] = provider

    first = _call(app, context).json()
    assert len(first["snapshot"]) == 1

    # Second request, nothing to report: must not see the first request's snapshot.
    collection.docs = []
    second = _call(app, context).json()
    assert second["snapshot"] == []
