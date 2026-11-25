"""Phase 2.6: NSEService quote, series resolution and history on the real fixtures."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot
from slbm_tracker.services import nse_service as nse_service_module
from slbm_tracker.services.nse_client import NSEBlockedError, NSEConnectionError
from slbm_tracker.services.nse_service import NSEService

FIXTURES = Path(__file__).parent / "fixtures" / "nse"
TODAY = datetime.date(2026, 10, 1)


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def _client(**overrides) -> AsyncMock:
    client = AsyncMock()
    client.fetch_slb_quote.return_value = _load("getquote_slbm_IRFC.json")
    client.fetch_slb_by_symbol.return_value = _load("live_analysis_slb_symbol_IRFC.json")
    client.fetch_series_master.return_value = _load("series_master.json")
    client.fetch_slb_history.return_value = _load("slb_history_IRFC.json")
    for name, value in overrides.items():
        setattr(getattr(client, name), "side_effect", value)
    return client


@pytest.fixture(autouse=True)
def _clear_series_cache():
    nse_service_module.reset_series_cache()
    yield
    nse_service_module.reset_series_cache()


async def test_quote_persists_every_live_series_with_code_and_family(db: Session):
    result = await NSEService(db, client=_client()).get_slb_quote("irfc", today=TODAY)

    assert result["source"] == "quote"
    assert len(result["series"]) == 24
    rows = db.query(SLBSnapshot).filter_by(symbol="IRFC").all()
    active = [q for q in result["series"] if q["is_active"]]
    assert len(rows) == len(active)
    by_code = {r.series_code: r for r in rows}
    assert (by_code["11"].series_family, by_code["XN"].series_family) == ("A", "B")
    assert by_code["11"].series == by_code["XN"].series == "Nov-2026"
    xn = by_code["XN"]
    assert xn.settlement_date == xn.expiry_date == datetime.date(2026, 11, 3)
    assert xn.settlement_estimated is False
    assert (xn.best_bid, xn.best_bid_qty, xn.best_ask, xn.best_ask_qty) == (0.35, 10850, 0.7, 2000)
    assert (xn.volume, xn.open_positions) == (1436482, 14820912)
    assert xn.source == "NSE_QUOTE"
    assert "R3" not in by_code  # special series with no quotes, trades or positions


async def test_transport_error_falls_back_to_the_symbol_view(db: Session):
    client = _client(fetch_slb_quote=NSEConnectionError("timed out"))
    result = await NSEService(db, client=client).get_slb_quote("IRFC", today=TODAY)

    assert result["source"] == "symbol_view"
    assert len(result["series"]) == 14
    assert all(q["series_code"] is None and q["series_family"] is None for q in result["series"])
    # Symbol-view rows cannot tell Series A from B: stored without a family.
    assert db.query(SLBSnapshot).filter(SLBSnapshot.series_family.isnot(None)).count() == 0


async def test_block_does_not_call_nse_again_and_serves_cached_rows(db: Session):
    await NSEService(db, client=_client()).get_slb_quote("IRFC", today=TODAY)
    client = _client(fetch_slb_quote=NSEBlockedError("403"))

    result = await NSEService(db, client=client).get_slb_quote("IRFC", today=TODAY)

    assert result["source"] == "cache" and result["blocked"] is True
    client.fetch_slb_by_symbol.assert_not_called()
    assert {"XN", "11"} <= {q["series_code"] for q in result["series"]}


async def test_block_can_be_raised_to_the_caller(db: Session):
    client = _client(fetch_slb_quote=NSEBlockedError("403"))
    with pytest.raises(NSEBlockedError):
        await NSEService(db, client=client).get_slb_quote("IRFC", fallback=False)


async def test_not_on_slb_is_an_empty_quote_not_an_error(db: Session):
    client = _client()
    client.fetch_slb_quote.return_value = {"data": [], "timestamp": [], "outPosDate": []}
    result = await NSEService(db, client=client).get_slb_quote("NOTSLB", today=TODAY)
    assert result["source"] == "quote" and result["series"] == []
    client.fetch_slb_by_symbol.assert_not_called()


async def test_resolve_series_maps_month_to_series_b_code(db: Session):
    service = NSEService(db, client=_client())
    assert await service.resolve_series("Oct-2026") == ("XO", "Oct-2026")
    assert await service.resolve_series("Nov-2026") == ("XN", "Nov-2026")
    assert await service.resolve_series("11") == ("11", "Nov-2026")
    assert await service.resolve_series("Dec-2099") == ("Dec-2099", "Dec-2099")


async def test_series_master_is_cached_for_six_hours(db: Session):
    client = _client()
    service = NSEService(db, client=client)
    t0 = datetime.datetime(2026, 10, 5, 4, 0, tzinfo=datetime.timezone.utc)
    await service.resolve_series("Oct-2026", now=t0)
    await service.resolve_series("Nov-2026", now=t0 + datetime.timedelta(hours=5, minutes=59))
    assert client.fetch_series_master.await_count == 1
    await service.resolve_series("Nov-2026", now=t0 + datetime.timedelta(hours=6, minutes=1))
    assert client.fetch_series_master.await_count == 2


async def test_failed_series_master_is_not_cached(db: Session):
    client = _client(fetch_series_master=NSEConnectionError("down"))
    service = NSEService(db, client=client)
    assert await service.resolve_series("Oct-2026") == ("Oct-2026", "Oct-2026")
    client.fetch_series_master.side_effect = None
    assert await service.resolve_series("Oct-2026") == ("XO", "Oct-2026")


async def test_history_is_parsed_and_sorted_oldest_first(db: Session):
    rows = await NSEService(db, client=_client()).get_slb_history(
        "IRFC", datetime.date(2026, 9, 1), datetime.date(2026, 10, 4)
    )
    assert len(rows) == 70
    assert rows[0]["trade_date"] <= rows[-1]["trade_date"] == datetime.date(2026, 10, 1)


async def test_when_quote_and_symbol_view_both_fail_the_cache_is_labelled_cache(db: Session):
    await NSEService(db, client=_client()).get_slb_quote("IRFC", today=TODAY)  # stored rows
    client = _client(fetch_slb_quote=NSEConnectionError("down"), fetch_slb_by_symbol=NSEConnectionError("down"))
    result = await NSEService(db, client=client).get_slb_quote("IRFC", today=TODAY)
    assert result["source"] == "cache" and result["blocked"] is False
    assert {"XN", "11"} <= {q["series_code"] for q in result["series"]}


async def test_cached_rows_are_ordered_by_settlement(db: Session):
    await NSEService(db, client=_client()).get_slb_quote("IRFC", today=TODAY)
    result = await NSEService(db, client=_client(fetch_slb_quote=NSEBlockedError("403"))).get_slb_quote("IRFC")
    dates = [q["settlement_date"] for q in result["series"]]
    assert dates == sorted(dates)
