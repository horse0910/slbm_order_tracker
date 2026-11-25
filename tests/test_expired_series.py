"""Phase 1.3: latest-snapshot readers ignore series whose expiry has passed.

The SBIN Sep-2026 series settled on 1 Sep 2026. On 1 Oct its last snapshot
must not feed alerts or the dashboard's best opportunities, while the live
Oct series (and a series expiring today) still do.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot, Stock
from slbm_tracker.services.alert_service import ALERT_YIELD_THRESHOLD, AlertService
from slbm_tracker.services.dashboard_service import DashboardService

OCT_1 = datetime.date(2026, 10, 1)
TS = datetime.datetime(2026, 10, 1, 6, 0)


def _snap(db: Session, series: str, expiry: datetime.date, yld: float) -> None:
    db.add(SLBSnapshot(series_family="B", symbol="SBIN", series=series, expiry_date=expiry,
                       annualized_yield=yld, best_bid=1.0, best_ask=1.01, timestamp=TS))


def _seed(db: Session) -> None:
    db.add(Stock(symbol="SBIN", quantity=100))
    _snap(db, "Sep-2026", datetime.date(2026, 9, 1), 40.0)   # settled 1 Sep
    _snap(db, "Oct-2026", datetime.date(2026, 10, 6), 5.0)
    db.commit()


def test_latest_snapshots_exclude_expired_series(db: Session):
    _seed(db)
    snaps = AlertService._latest_snapshots(db, "SBIN", today=OCT_1)
    assert [s.series for s in snaps] == ["Oct-2026"]


def test_series_expiring_today_is_still_included(db: Session):
    _seed(db)
    snaps = AlertService._latest_snapshots(db, "SBIN", today=datetime.date(2026, 10, 6))
    assert [s.series for s in snaps] == ["Oct-2026"]
    assert AlertService._latest_snapshots(db, "SBIN", today=datetime.date(2026, 10, 7)) == []


def test_yield_alert_does_not_fire_on_expired_series(db: Session):
    _seed(db)
    AlertService.create_alert(db, alert_type=ALERT_YIELD_THRESHOLD, threshold=10.0)
    results = AlertService.evaluate_all(db=db, today=OCT_1)
    assert results[0].condition_met is False


def test_opportunities_exclude_expired_series(db: Session):
    # Since Phase 3.5 the dashboard card reads opportunity_service, which needs
    # series codes and bid quantities; the expired Sep row must not appear.
    from slbm_tracker.services import opportunity_service

    _seed(db)
    for s in db.query(SLBSnapshot).all():
        s.series_code = {"Sep-2026": "X9", "Oct-2026": "XO"}[s.series]
        s.best_bid_qty, s.underlying_price = 1000, 10.0
    db.commit()
    board = opportunity_service.build(db, today=OCT_1, now=TS + datetime.timedelta(hours=1))
    assert [o["series_code"] for st in board["stocks"] for o in st["options"]] == ["XO"]
