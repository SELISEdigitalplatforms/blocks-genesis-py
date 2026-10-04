"""Signing algorithm a third-party provider uses, as configured rather than as claimed.

Ports genesis-net `Auth/JwtSigningAlgorithm.cs`.

The only other source for this is the token's own `alg` header, which the attacker
writes. Selecting a key source from it is the algorithm-confusion setup: take the RSA
public key, use its bytes as an HMAC secret, sign HS256. Configuration decides; the token
never does.
"""
from enum import IntEnum
from typing import Iterable, List, Optional, Sequence


class JwtSigningAlgorithm(IntEnum):
    """Persisted as an int, so these values are part of the stored contract: append new
    members, never renumber existing ones."""

    # Rows written before this field existed. For a field that selects a key source,
    # unset must be rejected rather than defaulted to something plausible.
    UNSPECIFIED = 0

    RS256 = 1
    RS384 = 2
    RS512 = 3

    ES256 = 4
    ES384 = 5
    ES512 = 6

    PS256 = 7
    PS384 = 8
    PS512 = 9

    HS256 = 10
    HS384 = 11
    HS512 = 12


_SYMMETRIC = frozenset(
    {JwtSigningAlgorithm.HS256, JwtSigningAlgorithm.HS384, JwtSigningAlgorithm.HS512}
)


def is_symmetric(algorithm: JwtSigningAlgorithm) -> bool:
    """True for the HMAC family, whose key is a shared secret rather than a published one."""
    return algorithm in _SYMMETRIC


def to_wire_name(algorithm: JwtSigningAlgorithm) -> str:
    """The `alg` header value, or empty for UNSPECIFIED."""
    return "" if algorithm == JwtSigningAlgorithm.UNSPECIFIED else algorithm.name


def to_wire_names(algorithms: Optional[Iterable[JwtSigningAlgorithm]]) -> List[str]:
    """The `alg` values validation will accept.

    UNSPECIFIED contributes nothing, so a misconfigured row yields an empty list and is
    rejected rather than silently widening to whatever the key type happens to support.
    """
    if algorithms is None:
        return []

    names: List[str] = []
    for algorithm in algorithms:
        name = to_wire_name(JwtSigningAlgorithm(algorithm))
        if name and name not in names:
            names.append(name)
    return names


def is_single_key_source(algorithms: Optional[Sequence[JwtSigningAlgorithm]]) -> bool:
    """Whether every configured algorithm draws its key from the same place.

    A provider mixing families would need both a JWKS and a secret, which is two
    independent signing authorities for one provider.
    """
    if not algorithms:
        return False

    values = [JwtSigningAlgorithm(a) for a in algorithms]

    if any(a == JwtSigningAlgorithm.UNSPECIFIED for a in values):
        return False

    return all(is_symmetric(a) for a in values) or not any(is_symmetric(a) for a in values)
