from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from enum import IntEnum

# Import BaseEntity from entities/base_entity.py
from entities.base_entity import BaseEntityPydantic

# CertificateStorageType enum
class CertificateStorageType(IntEnum):
    AZURE = 1
    FILESYSTEM = 2
    MONGODB = 3

# JwtTokenParameters class
class JwtTokenParameters(BaseModel):
    issuer: str = Field(alias="Issuer")
    subject: Optional[str] = Field(alias="Subject", default=None)
    audiences: List[str] = Field(alias="Audiences")
    public_certificate_path: Optional[str] = Field(alias="PublicCertificatePath", default=None)
    public_certificate_password: str = Field(alias="PublicCertificatePassword")
    private_certificate_password: str = Field(alias="PrivateCertificatePassword")
    certificate_storage_type: CertificateStorageType = Field(alias="CertificateStorageType")
    certificate_valid_for_number_of_days: int = Field(alias="CertificateValidForNumberOfDays")
    issue_date: datetime = Field(alias="IssueDate")

    class Config:
        extra = "ignore"
        allow_population_by_field_name = True
        use_enum_values = True

# Tenant class for MongoDB document mapping
class Tenant(BaseEntityPydantic):
    tenant_id: str = Field(alias="TenantId")
    is_accept_blocks_terms: bool = Field(alias="IsAcceptBlocksTerms")
    is_use_blocks_exclusively: bool = Field(alias="IsUseBlocksExclusively")
    is_production: bool = Field(alias="IsProduction")
    name: Optional[str] = Field(alias="Name", default=None)
    db_name: str = Field(alias="DBName")
    application_domain: str = Field(alias="ApplicationDomain")
    allowed_domains: List[str] = Field(alias="AllowedDomains")
    cookie_domain: str = Field(alias="CookieDomain")
    is_disabled: bool = Field(alias="IsDisabled")
    db_connection_string: str = Field(alias="DbConnectionString")
    tenant_salt: str = Field(alias="TenantSalt")
    jwt_token_parameters: JwtTokenParameters = Field(alias="JwtTokenParameters")
    is_root_tenant: bool = Field(alias="IsRootTenant")
    is_cookie_enable: bool = Field(alias="IsCookieEnable")
    is_domain_verified: bool = Field(alias="IsDomainVerified")

    class Config:
        extra = "ignore"
        allow_population_by_field_name = True