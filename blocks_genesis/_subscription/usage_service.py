"""Current usage, read straight from Mongo. The SubscriptionUsageCurrent read model already
carries the computed allowance per meter, so there is no period math on this side.

A caller has two sides: the organization pays for everyone, and a member may have a
balance of their own. Both are published under the same OrganizationId, so who answers is
a matter of which rows are asked for, and every read here names its side.
"""
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from blocks_genesis._subscription import repository
from blocks_genesis._subscription.models import (
    Entitlement,
    MemberSides,
    ScopedEntitlements,
    SubscriptionEntitlements,
    UsageResult,
    UsageSnapshot,
    UsageSubLimit,
)

logger = logging.getLogger(__name__)


def _optional_number(value: Any) -> Optional[float]:
    """A stored quantity as a float, or None when there is none to read. Decimal128 (what
    the meter writes now) and any plain number both work, and nothing truncates."""
    if value is None:
        return None
    to_decimal = getattr(value, "to_decimal", None)
    if to_decimal is not None:
        value = to_decimal()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float:
    """A balance as a float. An unreadable one reads as 0 -- a meter always has a number."""
    parsed = _optional_number(value)
    if parsed is None and value is not None:
        logger.warning("Unreadable usage quantity %r; treating as 0", value)
    return parsed if parsed is not None else 0.0


MAX_QUANTITY_SCALE = 6


def _scale(value: Any) -> int:
    """The meter's decimal places, clamped to what the API accepts. Unreadable reads as 0."""
    try:
        return max(0, min(MAX_QUANTITY_SCALE, int(value)))
    except (TypeError, ValueError):
        return 0


def _to_sub_limit(doc: Dict[str, Any]) -> UsageSubLimit:
    return UsageSubLimit(
        window=int(doc.get("Window") or 0),
        window_count=int(doc.get("WindowCount") or 1),
        rolling=bool(doc.get("Rolling")),
        behaviour=int(doc.get("Behaviour") or 0),
        quantity=_number(doc.get("Quantity")),
        used=_number(doc.get("Used")),
        remaining=_number(doc.get("Remaining")),
        exceeded=bool(doc.get("Exceeded")),
        window_start_utc=doc.get("WindowStartUtc"),
        window_end_utc=doc.get("WindowEndUtc"),
    )


def _to_result(doc: Dict[str, Any]) -> UsageResult:
    used = _number(doc.get("Used"))
    included = _number(doc.get("Included"))
    overage_allowed = bool(doc.get("OverageAllowed", True))
    scale = _scale(doc.get("QuantityScale"))
    return UsageResult(
        allowed=used <= included or overage_allowed,
        meter_key=doc.get("MeterKey") or "",
        used=used,
        remaining=_number(doc.get("Remaining")),
        overage=_number(doc.get("Overage")),
        replayed=False,
        quantity_scale=scale,
        is_fraction_allowed=scale > 0,
        overage_allowed=overage_allowed,
        sub_limits=[
            _to_sub_limit(item) for item in doc.get("SubLimits") or [] if isinstance(item, dict)
        ],
    )


def _to_entitlements(doc: Optional[Dict[str, Any]]) -> SubscriptionEntitlements:
    """A plan's terms. No document means no live subscription, which the empty model says."""
    if not doc:
        return SubscriptionEntitlements()
    return SubscriptionEntitlements(
        has_subscription=True,
        subscription_id=doc.get("SubscriptionId") or "",
        plan_id=doc.get("PlanId") or "",
        plan_code=doc.get("PlanCode") or "",
        features_json=doc.get("FeaturesJson"),
        entitlements=[
            Entitlement(
                key=entry.get("Key") or "",
                limit_kind=int(entry.get("LimitKind") or 0),
                limit=_optional_number(entry.get("Limit")),
                meter_key=entry.get("MeterKey"),
                unit_label=entry.get("UnitLabel"),
            )
            for entry in (doc.get("Entitlements") or [])
            if entry.get("Key")
        ],
    )


def _pick_entitlement_doc(
    docs: List[Dict[str, Any]], user_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Whose plan terms answer for one side.

    A member's row is preferred when one exists, and the organization's is the fallback --
    not a guess, but what the data forces: entitlements carry no UserId today, so a member
    is granted by the organization's plan. The preference is written now so the day
    blocks-utilities publishes a member's own row, nothing here has to change.
    """
    if user_id:
        own = next((doc for doc in docs if doc.get("UserId") == user_id), None)
        if own is not None:
            return own
    return next((doc for doc in docs if not doc.get("UserId")), None)


class SubscriptionUsageService:
    """Stateless: Mongo access via DbContext, which the app/worker wires up at startup."""

    @classmethod
    async def get_usage_current(
        cls,
        *,
        tenant_id: str,
        organization_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[List[UsageResult]]:
        """Every meter's balance for the current period.

        The organization's own rows by default; one member's with a user_id.
        [] means no live subscription; None means the read failed.
        """
        if not tenant_id or not organization_id:
            logger.error("get_usage_current: tenant_id and organization_id are required")
            return None

        try:
            docs = await repository.get_current_usage_docs(
                tenant_id, organization_id, user_id
            )
        except Exception:
            logger.exception("get_usage_current: Mongo read failed")
            return None

        return [_to_result(doc) for doc in docs]

    @classmethod
    async def get_entitlements_current(
        cls,
        *,
        tenant_id: str,
        organization_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[SubscriptionEntitlements]:
        """The plan terms behind one side's balances.

        None means the read failed. A model with `has_subscription` false means there is
        no live subscription -- the same split the balances keep between None and [].
        """
        docs = await cls._entitlement_docs(tenant_id, organization_id)
        if docs is None:
            return None
        return _to_entitlements(_pick_entitlement_doc(docs, user_id))

    @classmethod
    async def get_usage_snapshot(
        cls,
        *,
        tenant_id: str,
        organization_id: str,
        user_id: Optional[str] = None,
        entitlements: bool = True,
    ) -> UsageSnapshot:
        """The organization's balances, each named member's own, and the plan terms behind
        them.

        The reads stand alone: a member's side failing leaves the organization's intact, so
        a gate that falls back to the organization still has something to fall back to.

        Members come back keyed by user id. A member nobody asked about is absent from the
        map rather than present as empty -- "not asked", "asked and the read failed" and
        "asked and they have nothing" are three different answers.
        """
        wanted = [user_id] if user_id else []
        organization, member = await cls._split_usage(tenant_id, organization_id, wanted)
        # The plan terms are a second read. A caller on every request that only needs
        # balances -- the usage gate -- leaves them out, and reads them later only when a
        # model has to be checked.
        if not entitlements:
            return UsageSnapshot(organization=organization, member=member)

        # One read serves every side: the terms differ only in which row is picked.
        docs = await cls._entitlement_docs(tenant_id, organization_id)
        if docs is None:
            entitlements = ScopedEntitlements(
                member={user_id: None} if user_id else {}
            )
        else:
            entitlements = ScopedEntitlements(
                organization=_to_entitlements(_pick_entitlement_doc(docs, None)),
                member=(
                    {user_id: _to_entitlements(_pick_entitlement_doc(docs, user_id))}
                    if user_id
                    else {}
                ),
            )

        return UsageSnapshot(
            organization=organization, member=member, entitlements=entitlements
        )

    @classmethod
    async def _split_usage(
        cls, tenant_id: str, organization_id: str, user_ids: List[str]
    ) -> Tuple[Optional[List[UsageResult]], Dict[str, Optional[List[UsageResult]]]]:
        """Every side's balances from one read, told apart by UserId.

        A failed read is None on every side that was asked for, because none of them was
        answered. A read that returned nothing for a side is [] -- that side has no rows,
        which is an answer.
        """
        if not tenant_id or not organization_id:
            logger.error("usage snapshot: tenant_id and organization_id are required")
            return None, {u: None for u in user_ids}
        try:
            docs = await repository.get_scoped_usage_docs(
                tenant_id, organization_id, user_ids
            )
        except Exception:
            logger.exception("usage snapshot: Mongo read failed")
            return None, {u: None for u in user_ids}

        organization = [_to_result(d) for d in docs if not d.get("UserId")]
        member = {
            u: [_to_result(d) for d in docs if d.get("UserId") == u] for u in user_ids
        }
        return organization, member

    @classmethod
    async def get_members(
        cls,
        *,
        tenant_id: str,
        organization_id: str,
        user_ids: Sequence[str],
        usage: bool = True,
        entitlements: bool = True,
    ) -> MemberSides:
        """Several members' own answers, returned rather than merged into anything.

        For a caller holding a list of member ids that wants their data and nothing else --
        no snapshot to carry, and the organization's rows never read. However many members
        are asked for, the balances are one query and the plan terms another, so the cost
        does not grow with the list.

        `usage` and `entitlements` answer different questions -- what is left, and what is
        granted -- so either can be asked for alone and the other is not read.
        """
        wanted = [u for u in dict.fromkeys(user_ids) if u]
        sides = MemberSides()
        if not wanted:
            return sides

        if usage:
            if not tenant_id or not organization_id:
                logger.error("get_members: tenant_id and organization_id are required")
                sides.usage = {u: None for u in wanted}
            else:
                try:
                    docs = await repository.get_scoped_usage_docs(
                        tenant_id, organization_id, wanted, include_organization=False
                    )
                except Exception:
                    logger.exception("get_members: Mongo read failed")
                    sides.usage = {u: None for u in wanted}
                else:
                    sides.usage = {
                        u: [_to_result(d) for d in docs if d.get("UserId") == u]
                        for u in wanted
                    }

        if entitlements:
            docs = await cls._entitlement_docs(tenant_id, organization_id)
            sides.entitlements = {
                u: (
                    None
                    if docs is None
                    else _to_entitlements(_pick_entitlement_doc(docs, u))
                )
                for u in wanted
            }
        return sides

    @classmethod
    async def ensure_member(
        cls,
        snapshot: UsageSnapshot,
        *,
        tenant_id: str,
        organization_id: str,
        user_id: str,
        usage: bool = True,
        entitlements: bool = True,
    ) -> Optional[List[UsageResult]]:
        """Fill in one member's side of a snapshot, reading only what is missing.

        The same read as `get_members`, kept in the snapshot instead of handed back, for
        the case where the member a snapshot was built for turns out not to be the one that
        answers -- a trigger naming someone else, or a named payer. The organization's rows
        and the plan already in hand are left alone.

        Nothing is read twice. A key already holding an answer is kept, including `[]`,
        which says that member has no rows -- an answer, not a miss. A key holding None is
        a failed read rather than an answer, so that one is asked again.

        The snapshot is updated in place, and the member's rows are returned as it now
        holds them -- None when `usage` was not asked for.
        """
        if not user_id:
            return None
        want_usage = usage and snapshot.member.get(user_id) is None
        want_plan = entitlements and snapshot.entitlements.member.get(user_id) is None

        if want_usage or want_plan:
            sides = await cls.get_members(
                tenant_id=tenant_id,
                organization_id=organization_id,
                user_ids=[user_id],
                usage=want_usage,
                entitlements=want_plan,
            )
            snapshot.member.update(sides.usage)
            snapshot.entitlements.member.update(sides.entitlements)

        return snapshot.member.get(user_id) if usage else None

    @classmethod
    async def _entitlement_docs(
        cls, tenant_id: str, organization_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Live plan rows for the organization, or None when they could not be read."""
        if not tenant_id or not organization_id:
            logger.error("entitlements: tenant_id and organization_id are required")
            return None
        try:
            return await repository.get_current_entitlement_docs(tenant_id, organization_id)
        except Exception:
            logger.exception("entitlements: Mongo read failed")
            return None
