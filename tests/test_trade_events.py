"""Phase 2.13: trade events inferred from rises in a series' traded quantity.

NSE publishes no SLB trade tape. A rise in the day's traded quantity between
two sweeps of one series is one event (combined quantity, latest fee). No
change records nothing; a fall (new trading day) is a reset, never negative.

Uses *_synthetic.json copies of the real IRFC quote (see MANIFEST.json): only
series XN's totTradedQty (1,000 then 1,500) and, in t1, lastPrice differ.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBTradeEvent
from slbm_tracker.services.nse_service import NSEService

FIXTURES = Path(__file__).parent / "fixtures" / "nse"
TODAY = datetime.date(2026, 10, 5)
T0 = datetime.datetime(2026, 10, 5, 4, 0)  # 09:30 IST, naive UTC
T1 = T0 + datetime.timedelta(minutes=3)


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


async def _sweep(db: Session, fixture: str, at: datetime.datetime) -> dict:
    client = AsyncMock()
    client.fetch_slb_quote.return_value = _payload(fixture)
    return await NSEService(db, client=client).get_slb_quote("IRFC", today=TODAY, now=at)


async def test_rise_from_1000_to_1500_records_one_event_of_500_at_the_latest_fee(db: Session):
    first = await _sweep(db, "getquote_slbm_IRFC_t0_synthetic.json", T0)
    assert first["trade_events"] == []  # nothing to compare against yet
    second = await _sweep(db, "getquote_slbm_IRFC_t1_synthetic.json", T1)

    events = db.query(SLBTradeEvent).all()
    assert len(events) == 1
    e = events[0]
    assert (e.symbol, e.series_code, e.series_family) == ("IRFC", "XN", "B")
    assert (e.quantity, e.last_fee, e.traded_quantity_total) == (500, 0.33, 1500)
    assert e.occurred_at == T1 and e.trading_date == TODAY
    assert e.settlement_date == datetime.date(2026, 11, 3)
    assert [ev["quantity"] for ev in second["trade_events"]] == [500]


async def test_no_change_records_nothing(db: Session):
    await _sweep(db, "getquote_slbm_IRFC_t1_synthetic.json", T0)
    await _sweep(db, "getquote_slbm_IRFC_t1_synthetic.json", T1)
    assert db.query(SLBTradeEvent).count() == 0


async def test_a_fall_is_a_reset_never_a_negative_trade(db: Session):
    await _sweep(db, "getquote_slbm_IRFC_t1_synthetic.json", T0)
    result = await _sweep(db, "getquote_slbm_IRFC_t0_synthetic.json", T1)
    assert result["trade_events"] == []
    assert db.query(SLBTradeEvent).count() == 0


async def test_first_sweep_of_a_new_day_compares_with_nothing_from_yesterday(db: Session):
    yesterday = T0 - datetime.timedelta(days=1)
    await _sweep(db, "getquote_slbm_IRFC_t0_synthetic.json", yesterday)
    await _sweep(db, "getquote_slbm_IRFC_t1_synthetic.json", T0)
    assert db.query(SLBTradeEvent).count() == 0


async def test_ist_day_boundary_not_utc(db: Session):
    # 18:25 UTC = 23:55 IST on 5 Oct; 18:40 UTC = 00:10 IST on 6 Oct: a new day.
    await _sweep(db, "getquote_slbm_IRFC_t0_synthetic.json", datetime.datetime(2026, 10, 5, 18, 25))
    await _sweep(db, "getquote_slbm_IRFC_t1_synthetic.json", datetime.datetime(2026, 10, 5, 18, 40))
    assert db.query(SLBTradeEvent).count() == 0


async def test_series_are_compared_by_code_not_month(db: Session):
    # Series A '11' and Series B 'XN' share Nov-2026; only XN changed.
    await _sweep(db, "getquote_slbm_IRFC_t0_synthetic.json", T0)
    await _sweep(db, "getquote_slbm_IRFC_t1_synthetic.json", T1)
    assert {e.series_code for e in db.query(SLBTradeEvent).all()} == {"XN"}
