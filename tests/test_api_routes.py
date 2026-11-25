"""Unit tests for FastAPI endpoints."""

from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.api.routes.slb import get_nse_service
from slbm_tracker.services.nse_client import NSEClient
from slbm_tracker.services.nse_service import NSEService


def test_health_check_endpoint(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "ok"
    assert "version" in data


def test_portfolio_crud_endpoints(client: TestClient):
    # 1. Add Stock
    create_payload = {
        "symbol": "RELIANCE",
        "name": "Reliance Industries",
        "isin": "INE002A01018",
        "quantity": 100,
        "avg_buy_price": 2500.0,
        "is_slb_eligible": True,
        "notes": "Long term holding",
    }
    res_add = client.post("/api/portfolio", json=create_payload)
    assert res_add.status_code == 201
    stock_id = res_add.json()["id"]
    assert res_add.json()["symbol"] == "RELIANCE"

    # 2. List Portfolio
    res_list = client.get("/api/portfolio")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1

    # 3. Get Stock by ID
    res_get = client.get(f"/api/portfolio/{stock_id}")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == stock_id

    # 4. Update Stock
    res_update = client.put(f"/api/portfolio/{stock_id}", json={"quantity": 150, "avg_buy_price": 2550.0})
    assert res_update.status_code == 200
    assert res_update.json()["quantity"] == 150

    # 5. Delete Stock
    res_del = client.delete(f"/api/portfolio/{stock_id}")
    assert res_del.status_code == 204

    # 6. Verify Deleted
    res_get_deleted = client.get(f"/api/portfolio/{stock_id}")
    assert res_get_deleted.status_code == 404


def test_portfolio_import_csv_endpoint(client: TestClient, zerodha_console_csv: str):
    files = {"file": ("holdings.csv", zerodha_console_csv.encode("utf-8"), "text/csv")}
    response = client.post("/api/portfolio/import", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["total_parsed"] == 3
    assert data["added"] == 3
    assert len(data["stocks"]) == 3

    # Check that portfolio now returns 3 stocks
    res_list = client.get("/api/portfolio")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 3


def test_portfolio_import_rejects_non_csv_filename(client: TestClient):
    # A non-.csv upload is rejected with 400 (not a 500). The route guards
    # ``file.filename`` for both a wrong extension and a missing filename.
    files = {"file": ("holdings.txt", b"symbol,qty\nINFY,10\n", "text/plain")}
    response = client.post("/api/portfolio/import", files=files)
    assert response.status_code == 400
    assert ".csv" in response.json()["detail"]


def test_slb_live_symbol_endpoint(client: TestClient, db: Session, sample_nse_json: dict):
    mock_client = AsyncMock(spec=NSEClient)
    mock_client.fetch_slb_by_symbol.return_value = sample_nse_json

    nse_service = NSEService(db=db, client=mock_client)
    client.app.dependency_overrides[get_nse_service] = lambda: nse_service
    try:
        response = client.get("/api/slb/live/RELIANCE")
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "RELIANCE"
        assert data["contracts_count"] == 2
        assert data["contracts"][0]["series"] == "Oct-2026"
        assert data["contracts"][0]["annualized_yield"] == 8.55
    finally:
        client.app.dependency_overrides.pop(get_nse_service, None)


def test_slb_series_master_endpoint(client: TestClient, db: Session):
    mock_client = AsyncMock(spec=NSEClient)
    mock_client.fetch_series_master.return_value = {
        "data": {
            "Series A": [{"key": "10", "value": "Oct-2026"}]
        }
    }
    nse_service = NSEService(db=db, client=mock_client)
    client.app.dependency_overrides[get_nse_service] = lambda: nse_service
    try:
        response = client.get("/api/slb/series-master")
        assert response.status_code == 200
        assert "data" in response.json()
    finally:
        client.app.dependency_overrides.pop(get_nse_service, None)


def test_get_nse_service_closes_httpx_client(client: TestClient, monkeypatch):
    """Regression (MED M1): the NSEService dependency is an async generator that
    must close its httpx client in ``finally`` after every SLB request, so a
    connection/socket is not leaked per call. We patch NSEService in the route
    module (so the REAL dependency runs, not an override) and assert the created
    service's client.close() was awaited exactly once."""
    import slbm_tracker.api.routes.slb as slb_module

    created: list = []

    class _FakeClient:
        def __init__(self) -> None:
            self.close = AsyncMock()

    class _FakeService:
        def __init__(self, db) -> None:  # matches NSEService(db=db)
            self.client = _FakeClient()
            created.append(self)

        async def get_eligible_securities(self) -> list[str]:
            return ["RELIANCE", "INFY"]

    monkeypatch.setattr(slb_module, "NSEService", _FakeService)

    response = client.get("/api/slb/eligible")
    assert response.status_code == 200
    assert response.json() == ["RELIANCE", "INFY"]
    assert len(created) == 1
    created[0].client.close.assert_awaited_once()
