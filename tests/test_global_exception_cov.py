import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException
from blocks_genesis._middlewares.global_exception_middleware import GlobalExceptionHandlerMiddleware


@pytest.mark.asyncio
async def test_dispatch_reraises_http_exception():
    mw = GlobalExceptionHandlerMiddleware(MagicMock())
    async def call_next(req):
        raise HTTPException(status_code=404)
    with pytest.raises(HTTPException):
        await mw.dispatch(MagicMock(), call_next)
