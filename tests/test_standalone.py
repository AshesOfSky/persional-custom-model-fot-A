from fastapi.testclient import TestClient
from apps.api.app import create_app
from custom_model.application.watchlist_service import WatchlistService
from custom_model.infrastructure.watchlist_store import SQLiteWatchlistStore


def test_export_starts_without_personal_watchlist(tmp_path):
    app = create_app(watchlist_service=WatchlistService(SQLiteWatchlistStore(tmp_path / 'watchlist.sqlite3')))
    client = TestClient(app)
    response = client.get('/v1/watchlist/entries')
    assert response.status_code == 200
    assert response.json()['items'] == []


def test_only_market_page_routes_are_exposed():
    paths = set(create_app().openapi()['paths'])
    assert '/v1/market/bars/query' in paths
    assert '/v1/analysis/research' in paths
    assert '/v1/analysis/structures' in paths
    assert not any(part in path for path in paths for part in ('/monitor', '/alerts', '/backtests', '/portfolio', '/screeners', '/fanzong', '/cn-strategy'))
