import logging
from typing import List, Optional

import aiohttp

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._subscription.models import (
    Entitlement,
    EntitlementsSnapshot,
    UsageResult,
)

logger = logging.getLogger(__name__)


class SubscriptionClient:
    _instance: Optional["SubscriptionClient"] = None

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")
        self._session = aiohttp.ClientSession()

    @classmethod
    def initialize(cls, base_url: str) -> None:
        if cls._instance is None:
            cls._instance = cls(base_url)
            logger.info("SubscriptionClient singleton initialized.")

    @classmethod
    def get_instance(cls) -> "SubscriptionClient":
        if cls._instance is None:
            raise RuntimeError("SubscriptionClient not initialized. Call `initialize()` first.")
        return cls._instance

    async def close(self) -> None:
        await self._session.close()

    def _headers(self, tenant_id: Optional[str] = None, oauth_token: Optional[str] = None) -> dict:
        # oauth_token override lets callers pass an already-resolved bearer token
        # instead of relying on ctx.oauth_token.
        ctx = BlocksContextManager.get_context()
        resolved_token = oauth_token or (ctx.oauth_token if ctx else None)
        if not resolved_token:
            raise RuntimeError("No oauth_token available (no override, no context oauth_token)")
        resolved_tenant = tenant_id or (ctx.tenant_id if ctx else None)
        if not resolved_tenant:
            raise RuntimeError("No tenant_id/project key available (no override, no context tenant_id)")
        return {
            "Authorization": f"Bearer {resolved_token}",
            "x-blocks-key": resolved_tenant,
            "Content-Type": "application/json",
        }

    @staticmethod
    async def _read_json(resp: aiohttp.ClientResponse) -> Optional[dict]:
        # Tolerate a non-JSON error body (e.g. a gateway's HTML page on 502/503).
        try:
            return await resp.json()
        except (aiohttp.ContentTypeError, ValueError):
            return None

    @staticmethod
    def _is_subscription_not_found(body: Optional[dict]) -> bool:
        # "No subscription" can arrive as a 404, or as 200 + this error code.
        return bool(body) and (body.get("error") or {}).get("code") == "subscription_not_found"

    async def get_usage_current(
        self,
        *,
        tenant_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        oauth_token: Optional[str] = None,
    ) -> Optional[List[UsageResult]]:
        # Every meter's balance for the current period, not a gate. None means the
        # call failed (log only, don't raise) -- distinct from [] (no subscription).
        params = {"organizationId": organization_id} if organization_id else None
        async with self._session.get(
            f"{self._base_url}/api/subscription-usage/current",
            headers=self._headers(tenant_id, oauth_token),
            params=params,
        ) as resp:
            body = await self._read_json(resp)
            if resp.status == 404 or self._is_subscription_not_found(body):
                return []
            if resp.status >= 400:
                logger.error("SubscriptionClient.get_usage_current failed: %s %s", resp.status, body)
                return None
            return [
                UsageResult(
                    allowed=e["allowed"],
                    meter_key=e["meterKey"],
                    used=e["used"],
                    remaining=e["remaining"],
                    overage=e["overage"],
                    replayed=e["replayed"],
                )
                for e in (body or {}).get("data") or []
            ]

    async def get_entitlements(
        self,
        *,
        fresh: bool = False,
        tenant_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        oauth_token: Optional[str] = None,
    ) -> Optional[EntitlementsSnapshot]:
        # None means the call failed (log only, don't raise).
        params = {}
        if fresh:
            params["fresh"] = "true"
        if organization_id:
            params["organizationId"] = organization_id
        async with self._session.get(
            f"{self._base_url}/api/entitlements",
            headers=self._headers(tenant_id, oauth_token),
            params=params or None,
        ) as resp:
            body = await self._read_json(resp)
            if resp.status >= 400:
                logger.error("SubscriptionClient.get_entitlements failed: %s %s", resp.status, body)
                return None
            data = (body or {})["data"]
            return EntitlementsSnapshot(
                has_subscription=data["hasSubscription"],
                status=data.get("status"),
                plan_code=data.get("planCode"),
                entitlements=[
                    Entitlement(
                        key=e["key"],
                        allowed=e["allowed"],
                        reason=e["reason"],
                        limit_kind=e.get("limitKind"),
                        limit=e.get("limit"),
                        used=e.get("used"),
                        remaining=e.get("remaining"),
                    )
                    for e in data.get("entitlements", [])
                ],
            )

    async def record_usage(
        self,
        meter_key: str,
        idempotency_key: str,
        quantity: int = 1,
        enforce: bool = True,
        *,
        tenant_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        oauth_token: Optional[str] = None,
    ) -> Optional[UsageResult]:
        # None means the call failed (log only, don't raise).
        params = {"organizationId": organization_id} if organization_id else None
        payload = {
            "meterKey": meter_key,
            "idempotencyKey": idempotency_key,
            "quantity": quantity,
            "enforce": enforce,
        }
        async with self._session.post(
            f"{self._base_url}/api/subscription-usage",
            headers=self._headers(tenant_id, oauth_token),
            params=params,
            json=payload,
        ) as resp:
            body = await self._read_json(resp)
            if resp.status == 404 or self._is_subscription_not_found(body):
                logger.warning(
                    "SubscriptionClient.record_usage: no subscription or no such meter (%s), denying.",
                    meter_key,
                )
                return UsageResult(
                    allowed=False,
                    meter_key=meter_key,
                    used=0,
                    remaining=0,
                    overage=0,
                    replayed=False,
                )
            if resp.status >= 400:
                logger.error("SubscriptionClient.record_usage failed: %s %s", resp.status, body)
                return None
            data = (body or {})["data"]
            return UsageResult(
                allowed=data["allowed"],
                meter_key=data["meterKey"],
                used=data["used"],
                remaining=data["remaining"],
                overage=data["overage"],
                replayed=data["replayed"],
            )
