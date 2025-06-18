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

