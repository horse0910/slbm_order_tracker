"""Phase 2.8: lending views read Series B only, latest per (symbol, family, expiry).

The real IRFC quote (1 Oct 2026) is persisted through NSEService, so November
has two live rows: 11 (Series A) and XN (Series B). Legacy snapshots (no
family, from before Phase 2) and special series never reach lending views.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, SLBSnapshot, Stock
from slbm_tracker.services.alert_service import ALERT_ABOVE_AVERAGE, ALERT_YIELD_THRESHOLD, AlertService
from slbm_tracker.services.dashboard_service import DashboardService
from slbm_tracker.services.nse_service import NSEService
from slbm_tracker.services.rollover_service import RolloverService

FIXTURES = Path(__file__).parent / "fixtures" / "nse"
OCT_1 = datetime.date(2026, 10, 1)
# Newer than the persisted quote rows, so a reader without the family filter picks them.
TS = datetime.datetime.utcnow() + datetime.timedelta(hours=1)


@pytest.fixture()
async def irfc(db: Session) -> Stock:
    stock = Stock(symbol="IRFC", quantity=1000)
    db.add(stock)
    db.commit()
    client = AsyncMock()
    client.fetch_slb_quote.return_value = json.loads((FIXTURES / "getquote_slbm_IRFC.json").read_text())
    await NSEService(db, client=client).get_slb_quote("IRFC", today=OCT_1)
    # Legacy (no family) and special rows with tempting yields.
    db.add(SLBSnapshot(symbol="IRFC", series="Nov-2026", expiry_date=datetime.date(2026, 11, 3),
                       annualized_yield=50.0, ltp=9.0, timestamp=TS + datetime.timedelta(hours=1)))
    db.add(SLBSnapshot(symbol="IRFC", series="Oct-2026", series_code="R3", series_family="SPECIAL",
                       expiry_date=datetime.date(2026, 10, 6), annualized_yield=60.0, ltp=9.0, timestamp=TS))
    db.commit()
    return stock


async def test_latest_snapshots_are_series_b_only(db: Session, irfc: Stock):
    snaps = AlertService._latest_snapshots(db, "IRFC", today=OCT_1)
    assert snaps and all(s.series_family == "B" for s in snaps)
    november = [s for s in snaps if s.expiry_date == datetime.date(2026, 11, 3)]
    assert [s.series_code for s in november] == ["XN"]


async def test_opportunity_query_returns_only_the_series_b_row(db: Session, irfc: Stock):
    from slbm_tracker.services import opportunity_service

    board = opportunity_service.build(db, today=OCT_1, expiries=6)
    november = [o for s in board["stocks"] for o in s["options"] if o["settlement_date"] == datetime.date(2026, 11, 3)]
    assert [o["series_code"] for o in november] == ["XN"]  # not 11 (A), not the legacy row
    # The dashboard card lists actionable Series B options only: XD at 6.82% net.
    best = DashboardService.build_summary(db, today=OCT_1)["best_opportunities"]
    assert [(o["symbol"], o["series"], o["annualized_yield"]) for o in best] == [("IRFC", "XD", 6.82)]


async def test_yield_alert_ignores_legacy_and_special_rows(db: Session, irfc: Stock):
    AlertService.create_alert(db, alert_type=ALERT_YIELD_THRESHOLD, threshold=10.0)
    assert AlertService.evaluate_all(db=db, today=OCT_1)[0].condition_met is False


async def test_above_average_uses_series_b_only(db: Session, irfc: Stock):
    AlertService.create_alert(db, alert_type=ALERT_ABOVE_AVERAGE, threshold=20.0, stock_id=irfc.id)
    # The newest row is the legacy 50% one; it must not be the reference.
    result = AlertService.evaluate_all(db=db, today=OCT_1)[0]
    assert result.condition_met is False


async def test_rollover_options_are_future_series_b_only(db: Session, irfc: Stock):
    db.add(LendingContract(stock_id=irfc.id, series="Oct-2026", expiry_date=datetime.date(2026, 10, 6),
                           quantity_lent=100, lending_fee=0.5, status="ACTIVE", trade_date=OCT_1))
    db.commit()
    rec = RolloverService.advise(db, today=OCT_1)[0]
    assert rec.options
    codes = {o.series for o in rec.options}
    assert "Nov-2026" in codes
    assert all(o.annualized_yield != 50.0 for o in rec.options)  # legacy row excluded
    assert all(o.expiry_date > datetime.date(2026, 10, 6) for o in rec.options)
    assert len([o for o in rec.options if o.expiry_date == datetime.date(2026, 11, 3)]) == 1
