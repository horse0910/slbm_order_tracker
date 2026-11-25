"""Phase 5.4: per-rule cooldown on top of edge triggering.

A rule that re-crosses its threshold inside the cooldown (default 45 min) is
treated as triggered but delivers nothing new; after the cooldown it fires.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from slbm_tracker.db.models import Alert, NotificationDelivery, SLBSnapshot
from slbm_tracker.services import business_config
from slbm_tracker.services.alert_delivery_service import AlertDeliveryService
from slbm_tracker.services.alert_service import ALERT_YIELD_THRESHOLD, AlertService

TODAY = datetime.date(2026, 10, 5)
T0 = datetime.datetime(2026, 10, 5, 4, 0)


def _yield(db: Session, value: float, at: datetime.datetime) -> None:
    db.add(SLBSnapshot(symbol="IRFC", series="Nov-2026", series_code="XN", series_family="B",
                       expiry_date=datetime.date(2026, 11, 3), annualized_yield=value, timestamp=at))
    db.commit()


def _tick(db: Session, at: datetime.datetime):
    results = AlertService.evaluate_all(db, today=TODAY, now=at)
    fired = [r.alert_id for r in results if r.fired_now]
    AlertDeliveryService.deliver(db, fired)
    return results


def _crossing_sequence(db: Session, recross_after: datetime.timedelta):
    AlertService.create_alert(db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)
    _yield(db, 9.0, T0)
    first = _tick(db, T0)[0]
    _yield(db, 7.0, T0 + datetime.timedelta(minutes=5))
    cleared = _tick(db, T0 + datetime.timedelta(minutes=5))[0]
    at = T0 + recross_after
    _yield(db, 9.5, at)
    again = _tick(db, at)[0]
    return first, cleared, again


def test_recrossing_inside_the_cooldown_delivers_nothing_new(db: Session):
    first, cleared, again = _crossing_sequence(db, datetime.timedelta(minutes=20))
    assert first.fired_now and not cleared.condition_met
    assert again.condition_met and not again.fired_now and again.cooldown_suppressed
    alert = db.query(Alert).one()
    assert alert.last_triggered_at == T0  # the suppressed crossing is not a new fire
    assert alert.is_triggered is True      # held: it won't fire while the condition stays met
    assert db.query(NotificationDelivery).filter_by(channel="in_app").count() == 1


def test_recrossing_after_the_cooldown_fires(db: Session):
    _, _, again = _crossing_sequence(db, datetime.timedelta(minutes=46))
    assert again.fired_now and not again.cooldown_suppressed
    assert db.query(NotificationDelivery).filter_by(channel="in_app").count() == 2


def test_cooldown_zero_means_edge_triggering_only(db: Session, monkeypatch):
    monkeypatch.setattr(business_config, "_overrides", {"alert_cooldown_minutes": 0})
    _, _, again = _crossing_sequence(db, datetime.timedelta(minutes=20))
    assert again.fired_now


def test_cooldown_is_per_rule(db: Session):
    AlertService.create_alert(db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)
    _yield(db, 9.0, T0)
    _tick(db, T0)
    AlertService.create_alert(db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.5)  # new rule, never fired
    results = {r.threshold_used: r for r in _tick(db, T0 + datetime.timedelta(minutes=1))}
    assert results[8.5].fired_now and not results[8.0].fired_now


def test_cooldown_setting_is_editable(client):
    assert client.get("/api/settings").json()["alert_cooldown_minutes"] == 45
    assert client.put("/api/settings", json={"alert_cooldown_minutes": 90}).json()["alert_cooldown_minutes"] == 90
    assert client.put("/api/settings", json={"alert_cooldown_minutes": -1}).status_code == 422
