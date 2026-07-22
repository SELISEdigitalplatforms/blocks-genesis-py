import asyncio
import inspect
from blocks_genesis._cache.CacheClient import CacheClient


def test_cache_client_abstract_method_bodies():
    names = list(CacheClient.__abstractmethods__)
    overrides = {n: (lambda self, *a, **k: None) for n in names}
    Concrete = type('Concrete', (CacheClient,), overrides)
    inst = Concrete()
    for n in names:
        base = getattr(CacheClient, n)
        sig = inspect.signature(base)
        args = [
            'x' for pname, p in list(sig.parameters.items())[1:]
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)
        ]
        if inspect.iscoroutinefunction(base):
            asyncio.run(base(inst, *args))
        else:
            base(inst, *args)
