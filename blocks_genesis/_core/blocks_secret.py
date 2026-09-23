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
    # Optional placement connections, read only when a process provisions or probes a
    # cluster. Runtime routing follows the tenant record, never these. A blank value
    # means the tenant was placed on DatabaseConnectionString.
    DevDatabaseConnectionString: str = ""
    OtherDatabaseConnectionString: str = ""
    RootDatabaseName: str = ""

