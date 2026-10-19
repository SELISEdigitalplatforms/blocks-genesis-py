from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blocks_genesis._core import api

A = 'blocks_genesis._core.api.'
DOCS_PATHS = ['/openapi.json', '/docs', '/redoc', '/docs/oauth2-redirect', '/swagger/index.html']


def _client(show_docs, **app_kwargs):
    app = FastAPI(**app_kwargs)

    @app.get('/items')
    async def items():
        return ['a']

    with patch(A + 'FastAPIInstrumentor'):
        api.configure_genesis(app, show_docs=show_docs)
    return app, TestClient(app)


@pytest.mark.parametrize('path', DOCS_PATHS)
def test_docs_routes_are_404_when_docs_are_off(path):
    _, client = _client(show_docs=False)
    assert client.get(path).status_code == 404


@pytest.mark.parametrize('path', DOCS_PATHS)
def test_docs_routes_are_404_with_a_custom_openapi_url(path):
    _, client = _client(show_docs=False, openapi_url='/api/openapi.json')
    assert client.get(path).status_code == 404


def test_custom_openapi_url_is_404_when_docs_are_off():
    app, client = _client(show_docs=False, openapi_url='/api/openapi.json')
    tenant = MagicMock(tenant_id='t1', is_disabled=False, is_root_tenant=False)
    service = MagicMock(get_tenant=AsyncMock(return_value=tenant))
    with patch('blocks_genesis._middlewares.tenant_middleware.get_tenant_service', return_value=service):
        response = client.get('/api/openapi.json', headers={'x-blocks-key': 't1'})
    assert response.status_code == 404
    assert '/api/openapi.json' not in [getattr(r, 'path', None) for r in app.router.routes]


def test_docs_urls_are_cleared_when_docs_are_off():
    app, _ = _client(show_docs=False)
    assert (app.openapi_url, app.docs_url, app.redoc_url) == (None, None, None)


def test_docs_are_served_when_docs_are_on():
    _, client = _client(show_docs=True)
    schema = client.get('/openapi.json')
    assert schema.status_code == 200
    assert '/items' in schema.json()['paths']
    page = client.get('/swagger/index.html')
    assert page.status_code == 200
    assert page.headers['content-type'].startswith('text/html')
    assert client.get('/docs').status_code == 200


@pytest.mark.parametrize('show_docs', [True, False])
def test_other_routes_still_answer(show_docs):
    _, client = _client(show_docs=show_docs)
    assert client.get('/ping').json()['status'] == 'healthy'
    assert client.get('/items').json() == ['a']


def test_a_consumer_route_at_a_docs_path_is_kept():
    from fastapi import FastAPI
    from blocks_genesis._core.api import _remove_docs_routes

    app = FastAPI(docs_url=None)

    @app.get("/docs")
    async def own_docs():
        return {"own": True}

    _remove_docs_routes(app)

    assert any(getattr(r, "path", None) == "/docs" for r in app.router.routes)
    assert not any(getattr(r, "path", None) == "/openapi.json" for r in app.router.routes)
