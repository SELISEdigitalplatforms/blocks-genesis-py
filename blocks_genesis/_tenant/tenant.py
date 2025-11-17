from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from enum import IntEnum

from blocks_genesis._entities.base_entity import BaseEntity


# ------------------------------
# Certificate Storage Enum
# ------------------------------
class CertificateStorageType(IntEnum):
    AZURE = 1
    FILESYSTEM = 2
    MONGODB = 3


# ------------------------------
# JWT Token Parameters (MAIN)
# ------------------------------
class JwtTokenParameters(BaseModel):
    issuer: Optional[str] = Field(alias="Issuer", default="")
    subject: Optional[str] = Field(alias="Subject", default="")
    audiences: List[str] = Field(default_factory=list, alias="Audiences")
    public_certificate_path: Optional[str] = Field(alias="PublicCertificatePath", default="")
    public_certificate_password: Optional[str] = Field(alias="PublicCertificatePassword", default="")
    private_certificate_password: Optional[str] = Field(alias="PrivateCertificatePassword", default="")
    certificate_storage_type: CertificateStorageType = Field(
        alias="CertificateStorageType",
        default=CertificateStorageType.AZURE
    )
    certificate_valid_for_number_of_days: int = Field(
        alias="CertificateValidForNumberOfDays",
        default=365
    )
    issue_date: Optional[datetime] = Field(alias="IssueDate", default=None)

    class Config:
        extra = "ignore"
        validate_by_name = True
        use_enum_values = True


# ------------------------------
# Third Party JWT Token Parameters
# ------------------------------
class ThirdPartyJwtTokenParameters(BaseModel):
    provider_name: str = Field(alias="ProviderName", default="")
    issuer: str = Field(alias="Issuer", default="")
    subject: str = Field(alias="Subject", default="")
    audiences: List[str] = Field(alias="Audiences", default_factory=list)
    public_certificate_path: str = Field(alias="PublicCertificatePath", default="")
    jwks_url: str = Field(alias="JwksUrl", default="")
    public_certificate_password: str = Field(alias="PublicCertificatePassword", default="")
    cookie_key: str = Field(alias="CookieKey", default="")

    class Config:
        extra = "ignore"
        validate_by_name = True


# ------------------------------
# Tenant Entity
# ------------------------------
class Tenant(BaseEntity):
    tenant_id: Optional[str] = Field(alias="TenantId", default="")
    is_accept_blocks_terms: bool = Field(alias="IsAcceptBlocksTerms", default=False)
    is_use_blocks_exclusively: bool = Field(alias="IsUseBlocksExclusively", default=False)

    name: Optional[str] = Field(alias="Name", default=None)
    db_name: str = Field(alias="DBName", default="")
    application_domain: str = Field(alias="ApplicationDomain", default="")
    allowed_domains: List[str] = Field(alias="AllowedDomains", default_factory=list)
    cookie_domain: str = Field(alias="CookieDomain", default="")

    is_disabled: bool = Field(alias="IsDisabled", default=False)
    db_connection_string: str = Field(alias="DbConnectionString", default="")
    tenant_salt: str = Field(alias="TenantSalt", default="")

    jwt_token_parameters: Optional[JwtTokenParameters] = Field(alias="JwtTokenParameters", default=None)
    third_party_jwt_token_parameters: Optional[ThirdPartyJwtTokenParameters] = Field(
        alias="ThirdPartyJwtTokenParameters",
        default=None
    )

    is_root_tenant: bool = Field(alias="IsRootTenant", default=False)
    is_domain_verified: bool = Field(alias="IsDomainVerified", default=False)

    environment: Optional[str] = Field(alias="Environment", default="")
    tenant_group_id: Optional[str] = Field(alias="TenantGroupId", default="")
    custom_domain: Optional[str] = Field(alias="CustomDomain", default="")

    class Config:
        extra = "ignore"
        validate_by_name = True
