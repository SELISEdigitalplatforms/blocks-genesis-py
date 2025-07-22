from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from enum import IntEnum

from blocks_genesis._entities.base_entity import BaseEntity

class CertificateStorageType(IntEnum):
    AZURE = 1
    FILESYSTEM = 2
    MONGODB = 3

class JwtTokenParameters(BaseModel):
    issuer: Optional[str] = Field(alias="Issuer", default="")
    subject: Optional[str] = Field(alias="Subject", default=None)
    audiences: List[str] = Field(default_factory=list, alias="Audiences")
    public_certificate_path: Optional[str] = Field(alias="PublicCertificatePath", default=None)
    public_certificate_password: Optional[str] = Field(alias="PublicCertificatePassword", default="")
    private_certificate_password: Optional[str] = Field(alias="PrivateCertificatePassword", default="")
    certificate_storage_type: CertificateStorageType = Field(alias="CertificateStorageType", default=CertificateStorageType.AZURE)
    certificate_valid_for_number_of_days: int = Field(alias="CertificateValidForNumberOfDays", default=365)
    issue_date: Optional[datetime] = Field(alias="IssueDate", default=None)

    class Config:
        extra = "ignore"
        validate_by_name = True
        use_enum_values = True

class Tenant(BaseEntity):
    tenant_id: Optional[str] = Field(alias="TenantId", default="")
    is_accept_blocks_terms: Optional[bool] = Field(alias="IsAcceptBlocksTerms", default=False)
    is_use_blocks_exclusively: Optional[bool] = Field(alias="IsUseBlocksExclusively", default=False)
    is_production: Optional[bool] = Field(alias="IsProduction", default=False)
    name: Optional[str] = Field(alias="Name", default="")
    db_name: Optional[str] = Field(alias="DBName", default="")
    application_domain: Optional[str] = Field(alias="ApplicationDomain", default="")
    allowed_domains: List[str] = Field(default_factory=list, alias="AllowedDomains")
    cookie_domain: Optional[str] = Field(alias="CookieDomain", default="")
    is_disabled: Optional[bool] = Field(alias="IsDisabled", default=False)
    db_connection_string: Optional[str] = Field(alias="DbConnectionString", default="")
    tenant_salt: Optional[str] = Field(alias="TenantSalt", default="")
    jwt_token_parameters: Optional[JwtTokenParameters] = Field(alias="JwtTokenParameters", default=None)
    is_root_tenant: Optional[bool] = Field(alias="IsRootTenant", default=False)
    is_cookie_enable: Optional[bool] = Field(alias="IsCookieEnable", default=False)
    is_domain_verified: Optional[bool] = Field(alias="IsDomainVerified", default=False)
    environment: Optional[str] = Field(alias="Environment", default="")
    tenant_group_id: Optional[str] = Field(alias="TenantGroupId", default="")

    class Config:
        extra = "ignore"
        validate_by_name = True
