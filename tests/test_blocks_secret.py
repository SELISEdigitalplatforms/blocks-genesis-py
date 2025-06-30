from blocks_genesis._core.blocks_secret import BlocksSecret

def test_blocks_secret_defaults():
    secret = BlocksSecret()
    assert secret.CacheConnectionString == ""
    assert secret.ServiceName == ""

def test_blocks_secret_fields():
    secret = BlocksSecret(CacheConnectionString="foo", ServiceName="svc")
    assert secret.CacheConnectionString == "foo"
    assert secret.ServiceName == "svc" 