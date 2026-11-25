"""Phase 4.5: the ROLLOVER alert fires rollover_warn_days (5) before the
reverse leg, with the next Series B contract's bid in the message."""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, SLBSnapshot, Stock
from slbm_tracker.services.alert_service import ALERT_ROLLOVER, AlertService

SETTLES = datetime.date(2026, 11, 3)


def _seed(db: Session, origin=datetime.date(2026, 10, 1), with_bid=True) -> Stock:
    stock = Stock(symbol="IRFC", quantity=1000)
    db.add(stock)
    db.flush()
    db.add(LendingContract(stock_id=stock.id, series="Nov-2026", series_code="XN", expiry_date=SETTLES,
                           reverse_leg_date=SETTLES, quantity_lent=1000, lending_fee=0.35, status="ACTIVE",
                           trade_date=origin, origin_date=origin))
    if with_bid:
        db.add(SLBSnapshot(symbol="IRFC", series="Dec-2026", series_code="XD", series_family="B",
                           expiry_date=datetime.date(2026, 12, 1), settlement_date=datetime.date(2026, 12, 1),
                           best_bid=1.17, best_bid_qty=1376, ltp=1.17, underlying_price=77.06,
                           timestamp=datetime.datetime(2026, 10, 28, 6, 0), source="NSE_QUOTE"))
    db.commit()
    return stock


def _evaluate(db: Session, today: datetime.date):
    return AlertService.evaluate_all(db, today=today)[0]


def test_fires_five_days_before_the_reverse_leg_with_the_next_bid(db: Session):
    _seed(db)
    AlertService.create_alert(db, alert_type=ALERT_ROLLOVER)
    assert _evaluate(db, datetime.date(2026, 10, 28)).condition_met is False  # 6 days out
    result = _evaluate(db, datetime.date(2026, 10, 29))                        # 5 days out
    assert result.condition_met and result.fired_now and result.threshold_used == 5.0
    assert "IRFC XN settles 03 Nov 2026 (5 days)" in result.message
    assert "next XD bid ₹1.17 (1,376)" in result.message
    assert "33 of 365 days used" in result.message or "28 of 365 days used" in result.message


def test_without_a_later_bid_says_so(db: Session):
    _seed(db, with_bid=False)
    AlertService.create_alert(db, alert_type=ALERT_ROLLOVER)
    result = _evaluate(db, datetime.date(2026, 11, 1))
    assert result.condition_met and "no later Series B bid captured" in result.message


def test_blocked_by_the_cap_is_in_the_message(db: Session):
    _seed(db, origin=datetime.date(2025, 11, 20))  # Dec 1 2026 would be day 376
    AlertService.create_alert(db, alert_type=ALERT_ROLLOVER)
    result = _evaluate(db, datetime.date(2026, 11, 1))
    assert result.condition_met and "365-day cap" in result.message


def test_closed_contracts_and_past_settlement_do_not_fire(db: Session):
    _seed(db)
    AlertService.create_alert(db, alert_type=ALERT_ROLLOVER)
    assert _evaluate(db, datetime.date(2026, 11, 4)).condition_met is False  # settled yesterday
    db.query(LendingContract).one().status = "CLOSED"
    db.commit()
    assert _evaluate(db, datetime.date(2026, 11, 1)).condition_met is False


def test_warn_days_is_editable_and_a_rule_threshold_overrides_it(db: Session, client):
    _seed(db)
    assert client.put("/api/settings", json={"rollover_warn_days": 10}).status_code == 200
    AlertService.create_alert(db, alert_type=ALERT_ROLLOVER)
    assert _evaluate(db, datetime.date(2026, 10, 24)).condition_met is True  # 10 days out
    AlertService.create_alert(db, alert_type=ALERT_ROLLOVER, threshold=2)
    results = AlertService.evaluate_all(db, today=datetime.date(2026, 10, 24))
    assert {r.threshold_used: r.condition_met for r in results} == {10.0: True, 2.0: False}
