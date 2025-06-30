import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from blocks_genesis._middlewares.global_exception_middleware import GlobalExceptionHandlerMiddleware
from starlette.requests import Request

@pytest.mark.asyncio
@patch('blocks_genesis._middlewares.global_exception_middleware.Activity')
async def test_dispatch_normal(mock_activity):
    mock_activity.get_trace_id.return_value = 'traceid'
    middleware = GlobalExceptionHandlerMiddleware(MagicMock())
    request = MagicMock(spec=Request)
    call_next = AsyncMock(return_value='response')
    result = await middleware.dispatch(request, call_next)
    assert result == 'response'

@pytest.mark.asyncio
@patch('blocks_genesis._middlewares.global_exception_middleware.Activity')
async def test_dispatch_exception(mock_activity):
    mock_activity.get_trace_id.return_value = 'traceid'
    middleware = GlobalExceptionHandlerMiddleware(MagicMock())
    request = MagicMock(spec=Request)
    call_next = AsyncMock(side_effect=Exception('fail'))
    with patch.object(middleware, 'handle_exception', AsyncMock(return_value='handled')) as mock_handle:
        result = await middleware.dispatch(request, call_next)
        assert result == 'handled'

@pytest.mark.asyncio
@patch('blocks_genesis._middlewares.global_exception_middleware.Activity')
async def test_handle_exception_json_payload(mock_activity):
    mock_activity.get_trace_id.return_value = 'traceid'
    middleware = GlobalExceptionHandlerMiddleware(MagicMock())
    request = MagicMock(spec=Request)
    request.headers.get.return_value = 'application/json'
    request.body = AsyncMock(return_value=b'{"foo": "bar"}')
    result = await middleware.handle_exception(request, Exception('fail'))
    assert result.status_code == 500
    assert b'An error occurred' in result.body

@pytest.mark.asyncio
@patch('blocks_genesis._middlewares.global_exception_middleware.Activity')
async def test_handle_exception_non_json_payload(mock_activity):
    mock_activity.get_trace_id.return_value = 'traceid'
    middleware = GlobalExceptionHandlerMiddleware(MagicMock())
    request = MagicMock(spec=Request)
    request.headers.get.return_value = 'text/plain'
    request.body = AsyncMock(return_value=b'')
    result = await middleware.handle_exception(request, Exception('fail'))
    assert result.status_code == 500

@pytest.mark.asyncio
@patch('blocks_genesis._middlewares.global_exception_middleware.Activity')
async def test_handle_exception_body_too_long(mock_activity):
    mock_activity.get_trace_id.return_value = 'traceid'
    middleware = GlobalExceptionHandlerMiddleware(MagicMock())
    request = MagicMock(spec=Request)
    request.headers.get.return_value = 'application/json'
    request.body = AsyncMock(return_value=b'a' * 2000)
    result = await middleware.handle_exception(request, Exception('fail'))
    assert result.status_code == 500 