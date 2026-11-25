"""Phase 3.6: LENDING_OPPORTUNITY alert, edge-triggered; YIELD_THRESHOLD is gross."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot, Stock
from slbm_tracker.services.alert_service import (
    ALERT_LENDING_OPPORTUNITY,
    ALERT_YIELD_THRESHOLD,
    AlertService,
)
from slbm_tracker.services.nse_service import NSEService

FIXTURES = Path(__file__).parent / "fixtures" / "nse"
TODAY = datetime.date(2026, 10, 1)
SWEPT = datetime.datetime(2026, 10, 1, 11, 0)
NOW = SWEPT + datetime.timedelta(minutes=30)


@pytest.fixture()
async def irfc(db: Session) -> Stock:
    stock = Stock(symbol="IRFC", quantity=1000)
    db.add(stock)
    db.commit()
    client = AsyncMock()
    client.fetch_slb_quote.return_value = json.loads((FIXTURES / "getquote_slbm_IRFC.json").read_text())
    await NSEService(db, client=client).get_slb_quote("IRFC", today=TODAY, now=SWEPT)
    return stock


async def test_fires_once_with_the_best_ticket_then_stays_quiet(db: Session, irfc):
    AlertService.create_alert(db, alert_type=ALERT_LENDING_OPPORTUNITY)
    first = AlertService.evaluate_all(db, today=TODAY, now=NOW)[0]
    assert first.condition_met and first.fired_now
    assert first.threshold_used == 4.0  # min_net_yield_pct by default
    assert "IRFC XD nets 6.82% p.a." in first.message
    assert "IRFC | lend 1000 | XD | ₹1.17/share" in first.message
    second = AlertService.evaluate_all(db, today=TODAY, now=NOW)[0]
    assert second.condition_met and not second.fired_now  # edge-triggered


async def test_threshold_is_the_net_yield_floor(db: Session, irfc):
    AlertService.create_alert(db, alert_type=ALERT_LENDING_OPPORTUNITY, threshold=7.0)
    assert AlertService.evaluate_all(db, today=TODAY, now=NOW)[0].condition_met is False


async def test_scoped_to_one_holding(db: Session, irfc):
    other = Stock(symbol="SBIN", quantity=10)
    db.add(other)
    db.commit()
    AlertService.create_alert(db, alert_type=ALERT_LENDING_OPPORTUNITY, stock_id=other.id)
    assert AlertService.evaluate_all(db, today=TODAY, now=NOW)[0].condition_met is False


async def test_stale_quotes_never_fire(db: Session, irfc):
    AlertService.create_alert(db, alert_type=ALERT_LENDING_OPPORTUNITY)
    later = SWEPT + datetime.timedelta(hours=25)
    assert AlertService.evaluate_all(db, today=TODAY, now=later)[0].condition_met is False


def test_yield_threshold_message_says_gross(db: Session):
    db.add(Stock(symbol="SBIN", quantity=10))
    db.add(SLBSnapshot(symbol="SBIN", series="Nov-2026", series_code="XN", series_family="B",
                       expiry_date=datetime.date(2026, 11, 3), annualized_yield=9.0, timestamp=SWEPT))
    db.commit()
    AlertService.create_alert(db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)
    result = AlertService.evaluate_all(db, today=TODAY)[0]
    assert result.condition_met and "gross yield" in result.message
