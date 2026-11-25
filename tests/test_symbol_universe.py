"""Tests for the tracked SLB symbol universe: seeding, idempotency, retrieval,
and isolation from portfolio holdings."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import Stock, TrackedSymbol
from slbm_tracker.services.symbol_universe_service import (
    SEED_SYMBOLS,
    SymbolUniverseService,
)


def test_seed_inserts_all_configured_symbols(db: Session):
    """Seeding populates the universe with exactly the configured symbols."""
    added = SymbolUniverseService.seed_symbols(db)
    assert added == len(SEED_SYMBOLS)

    stored = {row.symbol for row in db.query(TrackedSymbol).all()}
    assert stored == set(SEED_SYMBOLS)
    # Every configured symbol is present.
    for sym in SEED_SYMBOLS:
        assert sym in stored


def test_seed_has_no_duplicate_rows(db: Session):
    """Each symbol appears exactly once (unique-per-symbol invariant)."""
    SymbolUniverseService.seed_symbols(db)
    rows = db.query(TrackedSymbol.symbol).all()
    symbols = [r[0] for r in rows]
    assert len(symbols) == len(set(symbols)), "duplicate tracked symbols found"
    assert len(symbols) == len(set(SEED_SYMBOLS))


def test_seed_is_idempotent(db: Session):
    """Re-running the seed does not create duplicates or add rows."""
    first = SymbolUniverseService.seed_symbols(db)
    count_after_first = db.query(TrackedSymbol).count()

    second = SymbolUniverseService.seed_symbols(db)
    count_after_second = db.query(TrackedSymbol).count()

    third = SymbolUniverseService.seed_symbols(db)
    count_after_third = db.query(TrackedSymbol).count()

    assert first == len(SEED_SYMBOLS)
    assert second == 0
    assert third == 0
    assert count_after_first == count_after_second == count_after_third == len(SEED_SYMBOLS)


def test_seed_collapses_in_list_and_case_duplicates(db: Session):
    """In-list repeats and case/whitespace variants seed a single row."""
    added = SymbolUniverseService.seed_symbols(db, [" reliance ", "RELIANCE", "Reliance", "TCS"])
    assert added == 2
    stored = {row.symbol for row in db.query(TrackedSymbol).all()}
    assert stored == {"RELIANCE", "TCS"}


def test_seeding_does_not_create_portfolio_holdings(db: Session):
    """Seeding the universe must NOT create Stock (holdings) rows.

    The universe and the portfolio are independent: tracking a symbol never
    implies owning it, so portfolio counts/quantities stay at zero.
    """
    assert db.query(Stock).count() == 0
    SymbolUniverseService.seed_symbols(db)
    # Universe populated...
    assert db.query(TrackedSymbol).count() == len(SEED_SYMBOLS)
    # ...but portfolio is untouched.
    assert db.query(Stock).count() == 0


def test_portfolio_totals_unaffected_by_seeding(client: TestClient, db: Session):
    """Portfolio listing and totals are not artificially changed by the universe."""
    # A real holding the user actually owns.
    db.add(Stock(symbol="RELIANCE", quantity=50, avg_buy_price=2450.5))
    db.commit()

    SymbolUniverseService.seed_symbols(db)

    res = client.get("/api/portfolio")
    assert res.status_code == 200
    holdings = res.json()
    # Only the genuinely-owned holding appears — not the 58 tracked symbols.
    assert len(holdings) == 1
    assert holdings[0]["symbol"] == "RELIANCE"
    assert holdings[0]["quantity"] == 50
    total_quantity = sum(h["quantity"] for h in holdings)
    assert total_quantity == 50


def test_symbols_endpoint_returns_seeded_universe(client: TestClient, db: Session):
    """The /api/symbols endpoint returns the seeded universe for selection."""
    SymbolUniverseService.seed_symbols(db)

    res = client.get("/api/symbols")
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == len(SEED_SYMBOLS)
    returned = {item["symbol"] for item in body["symbols"]}
    assert returned == set(SEED_SYMBOLS)
    # No fabricated market data is present on the universe payload.
    assert set(body["symbols"][0].keys()) == {"id", "symbol", "created_at"}


def test_symbols_endpoint_is_alphabetically_ordered(client: TestClient, db: Session):
    """Symbols are returned in a stable alphabetical order for the picker."""
    SymbolUniverseService.seed_symbols(db)
    res = client.get("/api/symbols")
    returned = [item["symbol"] for item in res.json()["symbols"]]
    assert returned == sorted(returned)


def test_symbols_endpoint_empty_before_seeding(client: TestClient):
    """With no seeding against the test DB, the endpoint reports an empty set.

    (Confirms the endpoint reads from the database rather than a hardcoded list.)
    """
    res = client.get("/api/symbols")
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 0
    assert body["symbols"] == []
