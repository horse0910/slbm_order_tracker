"""Phase 5C API tests: CSV export routes.

Pure serialization over existing services — these assert the HTTP contract
(``text/csv`` content-type, ``Content-Disposition`` filename), the exact header
rows, correct values for seeded data, and that an empty dataset yields a
header-only CSV (never a crash). No recompute is exercised here; the money math
is owned/tested by the P&L and analytics services.
"""

from __future__ import annotations

import csv
import datetime
import io

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot, Stock
from slbm_tracker.services.contract_service import ContractService, today_ist
from slbm_tracker.services.pnl_service import PnLService


def _parse(resp) -> list[list[str]]:
    """Parse a CSV response body into a list of rows (list of string cells)."""
    return list(csv.reader(io.StringIO(resp.text)))


def _seed_stock(db: Session, symbol: str = "RELIANCE", quantity: int = 100) -> Stock:
    stock = Stock(symbol=symbol, quantity=quantity, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def _seed_contract_with_pnl(db: Session):
    """A stock + a 40-days-out ACTIVE contract (qty 100 × ₹5) + its P&L record."""
    stock = _seed_stock(db)
    today = today_ist()
    contract = ContractService.create_contract(
        db=db,
        stock_id=stock.id,
        expiry_date=today + datetime.timedelta(days=40),
        quantity_lent=100,
        lending_fee=5.0,
        series="Nov-2026",
        trade_date=today,
    )
    PnLService.record_for_contract(db=db, contract_id=contract.id)
    return stock, contract


# --- pnl.csv ---------------------------------------------------------------


def test_pnl_csv_content_type_and_row(client: TestClient, db: Session):
    _seed_contract_with_pnl(db)
    resp = client.get("/api/export/pnl.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="pnl.csv"' in resp.headers["content-disposition"]

    rows = _parse(resp)
    assert rows[0] == [
        "contract_id", "symbol", "series", "gross_fee", "processing_charge",
        "gst", "dp_charge", "net_income", "tax_slab_pct", "estimated_tax",
        "post_tax_income", "created_at",
    ]
    assert len(rows) == 2  # header + one record
    record = rows[1]
    assert record[1] == "RELIANCE"
    assert record[2] == "Nov-2026"
    assert record[3] == "500.00"  # gross_fee = 100 × 5, fixed-2


def test_pnl_csv_empty_is_header_only(client: TestClient):
    resp = client.get("/api/export/pnl.csv")
    assert resp.status_code == 200
    rows = _parse(resp)
    assert len(rows) == 1  # header only, no crash


# --- contracts.csv ---------------------------------------------------------


def test_contracts_csv_derived_view(client: TestClient, db: Session):
    _seed_contract_with_pnl(db)
    resp = client.get("/api/export/contracts.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="contracts.csv"' in resp.headers["content-disposition"]

    rows = _parse(resp)
    header = rows[0]
    assert header[:6] == ["id", "symbol", "series", "trade_date", "expiry_date", "reverse_leg_date"]
    assert len(rows) == 2
    row = dict(zip(header, rows[1]))
    assert row["symbol"] == "RELIANCE"
    assert row["effective_status"] == "ACTIVE"  # 40 days out
    assert row["days_remaining"] == "40"
    assert row["lending_fee"] == "5.0"  # per-share RATE, not money-quantized
    assert row["lending_fee_income"] == "500.00"  # money total, fixed-2
    assert row["is_rollover_candidate"] == "false"


def test_contracts_csv_empty_is_header_only(client: TestClient):
    resp = client.get("/api/export/contracts.csv")
    assert resp.status_code == 200
    assert len(_parse(resp)) == 1


# --- analytics.csv ---------------------------------------------------------


def test_analytics_csv_two_sections(client: TestClient, db: Session):
    _seed_contract_with_pnl(db)
    db.add(SLBSnapshot(series_family="B",
        symbol="RELIANCE", expiry_date=datetime.date(2026, 11, 3),
        timestamp=datetime.datetime.utcnow(), annualized_yield=8.55,
        best_bid=4.0, best_ask=5.0, open_positions=100,
    ))
    db.commit()

    resp = client.get("/api/export/analytics.csv")
    assert resp.status_code == 200
    assert 'filename="analytics.csv"' in resp.headers["content-disposition"]
    text = resp.text
    assert "Income by financial year" in text
    assert "Yield by symbol" in text

    rows = _parse(resp)
    # FY income section carries the ₹500 gross fee somewhere in its data rows.
    assert any("500.00" in cell for row in rows for cell in row)
    # Yield section carries the RELIANCE latest yield.
    assert any(row[:2] == ["RELIANCE", "8.55"] for row in rows if len(row) >= 2)


def test_analytics_csv_empty_has_section_headers(client: TestClient):
    resp = client.get("/api/export/analytics.csv")
    assert resp.status_code == 200
    text = resp.text
    assert "Income by financial year" in text
    assert "financial_year" in text
    assert "Yield by symbol" in text
    assert "symbol,latest_yield,average_yield,samples" in text


# --- history.csv -----------------------------------------------------------


def _seed_snapshots(db: Session, symbol: str = "RELIANCE"):
    now = datetime.datetime.utcnow()
    db.add_all([
        SLBSnapshot(series_family="B", symbol=symbol, expiry_date=datetime.date(2026, 11, 3),
                    timestamp=now - datetime.timedelta(days=1),
                    annualized_yield=8.0, best_bid=4.0, best_ask=5.0, open_positions=100),
        SLBSnapshot(series_family="B", symbol=symbol, expiry_date=datetime.date(2026, 11, 3),
                    timestamp=now, annualized_yield=8.4, best_bid=4.1, best_ask=5.0, open_positions=120),
    ])
    db.commit()


def test_history_csv_symbol_scope(client: TestClient, db: Session):
    _seed_stock(db)
    _seed_snapshots(db)
    resp = client.get("/api/export/history.csv?symbol=RELIANCE&days=30")
    assert resp.status_code == 200
    assert 'filename="slb-history-reliance-30d.csv"' in resp.headers["content-disposition"]
    rows = _parse(resp)
    assert rows[0] == ["date", "annualized_yield", "spread", "open_positions", "samples"]
    assert len(rows) == 3  # header + two distinct days


def test_history_csv_portfolio_scope(client: TestClient, db: Session):
    _seed_stock(db)  # owned (quantity > 0) → included in portfolio aggregate
    _seed_snapshots(db)
    resp = client.get("/api/export/history.csv")
    assert resp.status_code == 200
    assert 'filename="slb-history-portfolio-30d.csv"' in resp.headers["content-disposition"]
    assert len(_parse(resp)) == 3  # header + two days


def test_history_csv_days_clamped_to_retention(client: TestClient):
    # Default retention is 90; a larger request clamps, reflected in the filename.
    resp = client.get("/api/export/history.csv?symbol=RELIANCE&days=200")
    assert resp.status_code == 200
    assert "-90d.csv" in resp.headers["content-disposition"]


def test_history_csv_unknown_symbol_is_header_only(client: TestClient):
    resp = client.get("/api/export/history.csv?symbol=NOPE")
    assert resp.status_code == 200
    assert len(_parse(resp)) == 1  # header only, never fabricated
