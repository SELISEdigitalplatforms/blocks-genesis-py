from blocks_genesis._tenant.tenant import CertificateStorageType, JwtTokenParameters, Tenant
from datetime import datetime

def test_certificate_storage_type_enum():
    assert CertificateStorageType.AZURE == 1
    assert CertificateStorageType.FILESYSTEM == 2
    assert CertificateStorageType.MONGODB == 3

def test_jwt_token_parameters_fields():
    params = JwtTokenParameters(
        Issuer='issuer',
        Subject='subject',
        Audiences=['aud'],
        PublicCertificatePath='path',
        PublicCertificatePassword='pubpass',
        PrivateCertificatePassword='privpass',
        CertificateStorageType=CertificateStorageType.AZURE,
        CertificateValidForNumberOfDays=10,
        IssueDate=datetime.now()
    )
    assert params.issuer == 'issuer'
    assert params.subject == 'subject'
    assert params.audiences == ['aud']
    assert params.public_certificate_path == 'path'
    assert params.public_certificate_password == 'pubpass'
    assert params.private_certificate_password == 'privpass'
    assert params.certificate_storage_type == CertificateStorageType.AZURE
    assert params.certificate_valid_for_number_of_days == 10
    assert isinstance(params.issue_date, datetime)

def test_tenant_model_fields():
    params = JwtTokenParameters(
        Issuer='issuer',
        Subject='subject',
        Audiences=['aud'],
        PublicCertificatePath='path',
        PublicCertificatePassword='pubpass',
        PrivateCertificatePassword='privpass',
        CertificateStorageType=CertificateStorageType.AZURE,
        CertificateValidForNumberOfDays=10,
        IssueDate=datetime.now()
    )
    tenant = Tenant(
        _id='id',
        CreatedAt=datetime.now(),
        UpdatedAt=datetime.now(),
        TenantId='tid',
        IsAcceptBlocksTerms=True,
        IsUseBlocksExclusively=False,
        IsProduction=True,
        Name='name',
        DBName='db',
        ApplicationDomain='app.dom',
        AllowedDomains=['a.com'],
        CookieDomain='cookie.dom',
        IsDisabled=False,
        DbConnectionString='conn',
        TenantSalt='salt',
        JwtTokenParameters=params,
        IsRootTenant=True,
        IsCookieEnable=True,
        IsDomainVerified=True
    )
    assert tenant.tenant_id == 'tid'
    assert tenant.is_accept_blocks_terms is True
    assert tenant.is_use_blocks_exclusively is False
    assert tenant.is_production is True
    assert tenant.name == 'name'
    assert tenant.db_name == 'db'
    assert tenant.application_domain == 'app.dom'
    assert tenant.allowed_domains == ['a.com']
    assert tenant.cookie_domain == 'cookie.dom'
    assert tenant.is_disabled is False
    assert tenant.db_connection_string == 'conn'
    assert tenant.tenant_salt == 'salt'
    assert isinstance(tenant.jwt_token_parameters, JwtTokenParameters)
    assert tenant.is_root_tenant is True
    assert tenant.is_cookie_enable is True
    assert tenant.is_domain_verified is True 