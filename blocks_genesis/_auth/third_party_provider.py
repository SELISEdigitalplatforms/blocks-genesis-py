"""One external identity provider a tenant trusts tokens from.

Ports genesis-net `Auth/ThirdPartyJwtProvider.cs`. Stored in the `JwtThirdPartyProviders`
collection in the root database.

A tenant may hold several, including two of the same kind sharing an issuer -- two
applications inside one Auth0 tenant, say. Selection narrows on `iss` + `aud` first and
falls back to the `x-blocks-idp` header only when those cannot tell two candidates apart.
Providers sharing both an issuer and an audience must carry equivalent privilege, because
nothing cryptographic distinguishes their tokens.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from blocks_genesis._auth.blocks_context import DEFAULT_ORGANIZATION
from blocks_genesis._auth.jwt_signing_algorithm import JwtSigningAlgorithm
from blocks_genesis._entities.base_entity import BaseEntity


class ThirdPartyClaimsMapping(BaseModel):
    """Which claim supplies each field of the Blocks context.

    Each value is a literal claim name first. Claim names are opaque and routinely
    contain dots -- every namespaced OIDC claim is a URI, such as
    `https://myapp.example.com/user_id` -- so a value is only split on `.` when no claim
    by that exact name exists. The split form addresses a property inside a claim whose
    value is a JSON object, as in Keycloak's `realm_access.roles`.
    """

    # "sub" resolves through the standard subject claim. The resolved value is suffixed
    # with _external to form the Blocks user id.
    user_id: str = Field(alias="UserId", default="")
    email: str = Field(alias="Email", default="")
    # "email" resolves through the standard email claim.
    user_name: str = Field(alias="UserName", default="")
    name: str = Field(alias="Name", default="")
    roles: str = Field(alias="Roles", default="")

    class Config:
        extra = "ignore"
        validate_by_name = True

    def is_configured(self) -> bool:
        """True when any field is mapped. An entirely blank mapping cannot identify anyone."""
        return any(
            value.strip()
            for value in (self.user_id, self.email, self.user_name, self.name, self.roles)
        )


class ThirdPartyJwtProvider(BaseEntity):
    tenant_id: str = Field(alias="TenantId", default="")

    # Stable identifier the x-blocks-idp header names. Unique within a tenant.
    key: str = Field(alias="Key", default="")
    provider_name: str = Field(alias="ProviderName", default="")
    is_active: bool = Field(alias="IsActive", default=False)

    # Matched against the token's iss exactly and ordinally. Auth0's carries a trailing
    # slash and Okta's does not; normalising would invite matching the wrong provider.
    issuer: str = Field(alias="Issuer", default="")

    # Empty disables audience validation entirely, which also collapses every provider
    # sharing this issuer into one candidate set.
    audiences: List[str] = Field(alias="Audiences", default_factory=list)

    # Pinned into validation, and also selects the key source: symmetric members read
    # signing_secret_cipher, the rest read jwks_url or public_certificate_path.
    algorithms: List[JwtSigningAlgorithm] = Field(alias="Algorithms", default_factory=list)

    # Key source for the asymmetric families. Public, fetched over HTTPS. Preferred over
    # a certificate wherever it exists, because it carries several keys and so survives
    # the provider rotating one.
    jwks_url: str = Field(alias="JwksUrl", default="")

    # The other asymmetric key source: a single public certificate, addressed either as
    # an absolute URL or as a path on the host's filesystem. A provider configures
    # exactly one -- holding both would give it two independent signing authorities.
    public_certificate_path: str = Field(alias="PublicCertificatePath", default="")

    # Passphrase for a PKCS#12 certificate, encrypted under the tenant salt. Empty is
    # the ordinary case: a bare .crt or .der holds only a public key.
    public_certificate_password_cipher: str = Field(
        alias="PublicCertificatePasswordCipher", default=""
    )

    # Key source for the HMAC family, encrypted under the tenant salt.
    signing_secret_cipher: str = Field(alias="SigningSecretCipher", default="")

    # Descriptive only. Nothing routes or validates on these three.
    certificate_subject: str = Field(alias="CertificateSubject", default="")
    certificate_thumbprint: str = Field(alias="CertificateThumbprint", default="")
    # Not consulted during validation: the certificate's own expiry is what is enforced,
    # read from the file. This copy is a stale snapshot for display.
    certificate_not_after: Optional[datetime] = Field(alias="CertificateNotAfter", default=None)

    # Stored for parity. Deliberately not read: genesis-net's cookie path still reads the
    # legacy Tenant.ThirdPartyJwtTokenParameters.CookieKey, and two runtimes looking in
    # different cookies for one request is worse than neither reading this.
    cookie_key: str = Field(alias="CookieKey", default="")

    # Organization every caller arriving through this provider acts in. Never blank.
    #
    # Distinct from BaseEntity.organization_id, which is row metadata saying where this
    # configuration document lives. This one names the scope granted to the tokens this
    # provider validates.
    #
    # "default" is NOT a narrow scope: consumers read it as tenant-wide, so a provider
    # left on the initial value grants the widest organization scope there is.
    default_organization_id: str = Field(
        alias="DefaultOrganizationId", default=DEFAULT_ORGANIZATION
    )

    claims_mapping: ThirdPartyClaimsMapping = Field(
        alias="ClaimsMapping", default_factory=ThirdPartyClaimsMapping
    )

    class Config:
        extra = "ignore"
        validate_by_name = True

    @field_validator("default_organization_id", mode="before")
    @classmethod
    def _normalise_organization(cls, value):
        """Blank is normalised away on assignment, so nothing that reads this has to
        guard against it.

        A document written before the field existed carries no element; one written by a
        form that left the field empty carries "". Both land here, once, rather than at
        each place that reads an organization -- some of which collapse a blank to
        "default" while others deny it outright, so a blank would leave the scope a
        caller receives depending on which layer read it.
        """
        if value is None or not str(value).strip():
            return DEFAULT_ORGANIZATION
        return str(value).strip()

    @field_validator("algorithms", mode="before")
    @classmethod
    def _drop_unknown_algorithms(cls, value):
        """A number this build does not know is dropped rather than raising, so one new
        row written by a newer service cannot lock every provider out of this process."""
        if not value:
            return []
        known = []
        for item in value:
            try:
                known.append(JwtSigningAlgorithm(item))
            except ValueError:
                continue
        return known
