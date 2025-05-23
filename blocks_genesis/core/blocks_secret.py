from pydantic import BaseModel


class BlocksSecret(BaseModel):
    CacheConnectionString: str = ""
    MessageConnectionString: str = ""
    LogConnectionString: str = ""
    MetricConnectionString: str = ""
    TraceConnectionString: str = ""
    LogDatabaseName: str = ""
    MetricDatabaseName: str = ""
    TraceDatabaseName: str = ""
    ServiceName: str = ""
    DatabaseConnectionString: str = ""
    RootDatabaseName: str = ""
    EnableHsts: bool = False
    SshHost: str = ""
    SshUsername: str = ""
    SshPassword: str = ""
    SshNginxTemplate: str = ""



blocks_secret_instance: BlocksSecret | None = None