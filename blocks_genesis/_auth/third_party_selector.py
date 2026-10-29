"""Picks which configured provider a token belongs to.

Ports genesis-net `Auth/ThirdPartyProviderSelector.cs`.

Narrows cryptographically first and asks the client only when that cannot separate two
candidates. Reading `iss` and `aud` from an unvalidated token is safe here because they
only select a key set -- validation then re-checks both against the chosen provider, so a
forged issuer routes to a configuration whose keys will not verify the signature.

Matching is exact and case-sensitive. Auth0's issuer carries a trailing slash and Okta's
does not; normalising would invite matching the wrong provider.

Whether the token carries an `iss` decides which set of providers it can reach at all,
and the two sets are disjoint. A token with an issuer can only reach providers declaring
that exact issuer; a token without one can only reach providers declaring no issuer. So a
blank issuer is NOT a wildcard -- it narrows a provider to the tokens that name nobody.

That is deliberately the opposite of how an empty audience list behaves. Audience is a
filter applied within an already-identified sender, so an empty one means "do not
filter". Issuer IS the sender's identity, and widening that would make one provider a
catch-all offered every token from every other issuer -- for an HMAC provider, "offered"
means its shared secret gets tried against tokens it was never meant to see.
"""
from enum import IntEnum
from typing import Collection, List, NamedTuple, Optional, Sequence

from blocks_genesis._auth.third_party_provider import ThirdPartyJwtProvider


class ThirdPartyProviderSelection(IntEnum):
    """Why selection ended the way it did. Drives the log, not the control flow."""

    SELECTED = 0
    NO_PROVIDERS = 1

    # No provider is configured for the token's issuer. Usually a Blocks token, or a typo.
    ISSUER_UNMATCHED = 2

    AUDIENCE_UNMATCHED = 3

    # Several providers are indistinguishable and no x-blocks-idp header was sent.
    AMBIGUOUS_NO_HEADER = 4

    AMBIGUOUS_HEADER_UNMATCHED = 5

    # The token carries no iss at all, and no provider is configured to receive such
    # tokens. Kept apart from ISSUER_UNMATCHED because the two deserve opposite treatment
    # in a log: an unrecognised issuer is the everyday case, so it stays quiet, while a
    # token with no issuer whatsoever is not something this platform mints.
    ISSUER_ABSENT_UNMATCHED = 6


class ThirdPartyProviderResult(NamedTuple):
    provider: Optional[ThirdPartyJwtProvider]
    outcome: ThirdPartyProviderSelection
    candidate_count: int

    @property
    def is_selected(self) -> bool:
        return self.provider is not None


def select(
    providers: Optional[Sequence[ThirdPartyJwtProvider]],
    issuer: Optional[str],
    audiences: Optional[Collection[str]],
    header_key: Optional[str],
) -> ThirdPartyProviderResult:
    if not providers:
        return ThirdPartyProviderResult(None, ThirdPartyProviderSelection.NO_PROVIDERS, 0)

    # A token that names no issuer can only reach a provider that declares none. Not a
    # wildcard match against every provider -- see the asymmetry note on this module.
    if not issuer or not issuer.strip():
        issuerless = [p for p in providers if not p.issuer or not p.issuer.strip()]
        if not issuerless:
            return ThirdPartyProviderResult(
                None, ThirdPartyProviderSelection.ISSUER_ABSENT_UNMATCHED, 0
            )
        return _narrow(issuerless, audiences, header_key)

    by_issuer = [p for p in providers if p.issuer == issuer]

    # Deliberately no fallback to the issuer-less providers. A token whose issuer nothing
    # claims fails closed rather than being handed to whichever provider left it blank.
    if not by_issuer:
        return ThirdPartyProviderResult(None, ThirdPartyProviderSelection.ISSUER_UNMATCHED, 0)

    return _narrow(by_issuer, audiences, header_key)


def _narrow(
    matched: List[ThirdPartyJwtProvider],
    audiences: Optional[Collection[str]],
    header_key: Optional[str],
) -> ThirdPartyProviderResult:
    """Reduce an already issuer-matched set to one provider, by audience then by header.

    Shared by both lanes so they cannot drift. An issuer-less set reaches here with a
    token that has no audience either, which every provider's audience rule accepts -- so
    for that lane this collapses to "one candidate selects itself, several need the
    header", the same bargain the issuer lane makes.
    """
    candidates = [p for p in matched if _audience_matches(p, audiences)]

    if not candidates:
        return ThirdPartyProviderResult(
            None, ThirdPartyProviderSelection.AUDIENCE_UNMATCHED, len(matched)
        )

    if len(candidates) == 1:
        # The common case: issuer and audience already identify one provider, so the
        # header is never read and callers need not send it.
        return ThirdPartyProviderResult(candidates[0], ThirdPartyProviderSelection.SELECTED, 1)

    # Several providers share both issuer and audience, so nothing cryptographic tells
    # their tokens apart and the header alone decides which claim mapping applies. Those
    # providers must carry equivalent privilege -- enforced where they are saved, not here.
    if not header_key or not header_key.strip():
        return ThirdPartyProviderResult(
            None, ThirdPartyProviderSelection.AMBIGUOUS_NO_HEADER, len(candidates)
        )

    named = next((p for p in candidates if p.key == header_key), None)

    if named is None:
        return ThirdPartyProviderResult(
            None, ThirdPartyProviderSelection.AMBIGUOUS_HEADER_UNMATCHED, len(candidates)
        )

    return ThirdPartyProviderResult(
        named, ThirdPartyProviderSelection.SELECTED, len(candidates)
    )


def _audience_matches(
    provider: ThirdPartyJwtProvider, audiences: Optional[Collection[str]]
) -> bool:
    """An empty Audiences list disables audience validation for that provider, so it
    matches any token from its issuer. That is also what collapses several providers into
    one candidate set."""
    if not provider.audiences:
        return True

    return bool(audiences) and any(a in provider.audiences for a in audiences)
