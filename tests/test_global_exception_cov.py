import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from blocks_genesis._middlewares.global_exception_middleware import GlobalExceptionHandlerMiddleware

G = 'blocks_genesis._middlewares.global_exception_middleware.'


@pytest.mark.asyncio
async def test_dispatch_reraises_http_exception():
    mw = GlobalExceptionHandlerMiddleware(MagicMock())
    async def call_next(req):
        raise HTTPException(status_code=404)
    with pytest.raises(HTTPException):
        await mw.dispatch(MagicMock(), call_next)


@pytest.mark.asyncio
@patch(G + 'Activity')
async def test_handle_exception_empty_json_body(mock_activity):
    mock_activity.get_trace_id.return_value = 'tid'
    mw = GlobalExceptionHandlerMiddleware(MagicMock())
    request = MagicMock()
    request.headers.get.return_value = 'application/json'
    request.body = AsyncMock(return_value=b'   ')
    request.url = 'http://x'; request.method = 'GET'
    resp = await mw.handle_exception(request, Exception('boom'))
    assert resp.status_code == 500


@pytest.mark.asyncio
@patch(G + 'Activity')
async def test_handle_exception_body_read_error(mock_activity):
    mock_activity.get_trace_id.return_value = 'tid'
    mw = GlobalExceptionHandlerMiddleware(MagicMock())
    request = MagicMock()
    request.headers.get.return_value = 'application/json'
    request.body = AsyncMock(side_effect=Exception('fail'))
    request.url = 'http://x'; request.method = 'GET'
    resp = await mw.handle_exception(request, Exception('boom'))
    assert resp.status_code == 500
