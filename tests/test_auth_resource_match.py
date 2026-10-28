"""A permission opens only the endpoint it was granted for, and a used-up quota refuses the call.

Story: Lena holds the "reports" permission. The billing endpoint must stay closed to her, while
Karim, who holds "billing", gets in. Once the billing quota is used up, even Karim is refused.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blocks_genesis._auth import auth

AU = 'blocks_genesis._auth.auth.'


def _matches(doc, query):
    for key, want in query.items():
        if key == '$or':
            if not any(_matches(doc, q) for q in want):
                return False
        elif key == '$and':
            if not all(_matches(doc, q) for q in want):
                return False
        elif isinstance(want, dict) and '$in' in want:
            have = doc.get(key)
            have = have if isinstance(have, list) else [have]
            if not set(have) & set(want['$in']):
                return False
        elif doc.get(key) != want:
            return False
    return True


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _matches(d, query))

    async def find_one(self, query):
        return next((d for d in self.docs if _matches(d, query)), None)


def _db(docs):
    db = MagicMock()
    db.get_collection = AsyncMock(return_value=FakeCollection(docs))
    return db


PERMISSIONS = [
    {'OrganizationId': 'org', 'Resource': 'reports', 'Roles': ['analyst']},
    {'OrganizationId': 'org', 'Resource': 'billing', 'Roles': ['accountant']},
]


@pytest.fixture
def signed_in():
    ctx = MagicMock(impersonated=False, tenant_id='tid', organization_id='org')
    with patch(AU + 'BlocksContextManager') as bcm:
        bcm.get_context.return_value = ctx
        yield ctx


async def test_a_permission_for_another_resource_is_refused(signed_in):
    assert await auth._check_permission('billing', [], ['reports'], 'tid', _db(PERMISSIONS)) is False


async def test_the_permission_for_the_resource_is_accepted(signed_in):
    assert await auth._check_permission('billing', [], ['billing'], 'tid', _db(PERMISSIONS)) is True


async def test_a_permission_with_no_matching_rule_is_refused(signed_in):
    docs = [PERMISSIONS[0]]
    assert await auth._check_permission('billing', [], ['billing'], 'tid', _db(docs)) is False


async def test_a_role_for_the_resource_is_still_accepted(signed_in):
    assert await auth._check_permission('billing', ['accountant'], [], 'tid', _db(PERMISSIONS)) is True


async def test_a_role_for_another_resource_is_refused(signed_in):
    assert await auth._check_permission('billing', ['analyst'], [], 'tid', _db(PERMISSIONS)) is False


async def test_a_used_up_quota_refuses_the_call():
    ctx = MagicMock(tenant_id='tid')
    db = _db([{'Resource': 'billing', 'Limit': 5, 'Usage': 5}])
    assert await auth._check_quota(ctx, 'billing', db) is False


async def test_a_quota_with_room_left_allows_the_call():
    ctx = MagicMock(tenant_id='tid')
    db = _db([{'Resource': 'billing', 'Limit': 5, 'Usage': 2}])
    assert await auth._check_quota(ctx, 'billing', db) is True


async def test_no_quota_configured_allows_the_call():
    ctx = MagicMock(tenant_id='tid')
    db = _db([{'Resource': 'reports', 'Limit': 1, 'Usage': 1}])
    assert await auth._check_quota(ctx, 'billing', db) is True


async def test_the_quota_for_another_resource_is_not_charged_to_this_one():
    ctx = MagicMock(tenant_id='tid')
    db = _db([{'Resource': 'reports', 'Limit': 1, 'Usage': 1}, {'Resource': 'billing', 'Limit': 9, 'Usage': 1}])
    assert await auth._check_quota(ctx, 'billing', db) is True
