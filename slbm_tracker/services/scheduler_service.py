"""Background alert-evaluation scheduler (Phase 4).

Runs the existing evaluate -> deliver chain on a fixed interval so the app
monitors unattended, then pushes each newly-fired alert to connected WebSocket
clients in real time. It ORCHESTRATES only — it performs no calculation and
reuses the exact chain the manual ``POST /api/alerts/evaluate`` route uses::

    (optional NSE snapshot refresh) -> AlertService.evaluate_all
        -> AlertDeliveryService.deliver -> ws_manager.broadcast

Design guarantees:

* One long-lived :class:`NSEClient` is reused across ticks (preserves cookie
  warming / connection reuse) and closed in :meth:`stop`.
* A tick opens its own ``SessionLocal()`` and always closes it. The tick body
  catches ``Exception`` (NOT ``BaseException``) so one bad NSE fetch or DB
  hiccup is isolated and never kills the loop, while ``asyncio.CancelledError``
  still propagates for a clean shutdown.
* Each per-symbol NSE refresh is individually guarded, so one failing symbol
  never aborts the rest of the tick. :class:`NSEService` already degrades to
  cached snapshots on ``NSEError``.
* The synchronous evaluate/deliver/prune body (whose delivery step may open a
  *blocking* ``smtplib.SMTP`` connection) is offloaded to a worker thread via
  ``run_in_executor``, so a slow/unreachable SMTP server can never stall the
  event loop that serves ``/api/ws`` and HTTP. Only the WS broadcast stays on
  the loop.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from slbm_tracker.api.ws_manager import ws_manager
from slbm_tracker.config import Settings, get_settings
from slbm_tracker.db.models import Alert
from slbm_tracker.db.session import SessionLocal
from slbm_tracker.services.alert_delivery_service import AlertDeliveryService
from slbm_tracker.services import backup_service, business_config
from slbm_tracker.services.alert_service import AlertService
from slbm_tracker.services.contract_service import IST, ContractService, today_ist
from slbm_tracker.services.nse_client import NSEBlockedError, NSEConnectionError, NSEError, NSEClient
from slbm_tracker.services.nse_service import NSEService, _row_from_market_data
from slbm_tracker.services.nse_parser import NSEDataParser, series_family
from slbm_tracker.utils.slb_calendar import settlement_date_for_label
from slbm_tracker.services.snapshot_retention_service import SnapshotRetentionService
from slbm_tracker.services.portfolio_service import PortfolioService

logger = logging.getLogger(__name__)


# NSE SLB session in IST. No holiday calendar (decided 4 Oct 2026): on a weekday
# holiday a sweep just returns the previous session's figures.
MARKET_OPEN = datetime.time(9, 0)
MARKET_CLOSE = datetime.time(17, 15)
# Phase 7.4: the daily backup runs on the first tick at or after this (IST).
BACKUP_AFTER = datetime.time(17, 20)
# House rule 9: on a 403, stop the sweep and leave NSE alone for this long.
BLOCK_BACKOFF = datetime.timedelta(minutes=15)


def is_market_hours(now: datetime.datetime) -> bool:
    """True from 09:00 to 17:15 IST inclusive, Monday to Friday. ``now`` must be tz-aware."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("is_market_hours needs a timezone-aware datetime.")
    local = now.astimezone(IST)
    return local.weekday() < 5 and MARKET_OPEN <= local.time().replace(second=0, microsecond=0) <= MARKET_CLOSE


def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


class AlertSchedulerService:
    """Owns the background tick loop and the live-push message contract."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        nse_client: Optional[NSEClient] = None,
        clock: Callable[[], datetime.datetime] = _now_ist,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings or get_settings()
        self._clock = clock
        # Phase 7.3: wall clock (it keeps counting while the Mac sleeps, unlike
        # time.monotonic on macOS) to notice a long pause between ticks.
        self._wall = wall_clock
        self._last_tick_wall: Optional[float] = None
        self._expected_gap: Optional[float] = None
        # One long-lived client reused across ticks; closed in stop().
        self._nse_client = nse_client or NSEClient(settings=self._settings)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # Rotation position into the polled-symbol list, so a capped tick
        # resumes where the previous one stopped instead of at the top.
        self._poll_cursor = 0
        # Set when NSE answers 403: no NSE calls before this moment (tz-aware).
        self._backoff_until: Optional[datetime.datetime] = None
        # Last live sweep (Phase 2.13), read by GET /api/portfolio/live.
        self.sweep_status: Optional[dict[str, Any]] = None
        self.last_summary = None
        self._month_options = None
        self._month_options_at = None

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    @staticmethod
    def build_alert_fired_message(alert: Alert) -> dict[str, Any]:
        """Backend-computed ``alert_fired`` frame built from the persisted row.

        Shared by the auto-tick and the manual evaluate route so both emit an
        identical envelope. Timestamps are naive-UTC ISO (matching REST).
        """
        return {
            "type": "alert_fired",
            "alert_id": alert.id,
            "alert_type": alert.alert_type,
            "stock_id": alert.stock_id,
            "message": alert.trigger_message,
            "last_triggered_at": (
                alert.last_triggered_at.isoformat() if alert.last_triggered_at else None
            ),
        }

    def _next_batch(self, symbols: list[str]) -> list[str]:
        """Take up to ``scheduler_max_symbols_per_tick`` symbols from the cursor, wrapping."""
        n = len(symbols)
        if n == 0:
            return []
        cap = self._settings.scheduler_max_symbols_per_tick
        size = n if not cap or cap <= 0 else min(cap, n)
        start = self._poll_cursor % n
        self._poll_cursor = (start + size) % n
        return [symbols[(start + i) % n] for i in range(size)]

    def _maybe_backup(self, now: datetime.datetime) -> Optional[str]:
        """Phase 7.4: once per IST day, after 17:20, back the database up.
        Returns the new file's path, or None. Never raises."""
        if not self._settings.backups_enabled:
            return None
        local = now.astimezone(IST)
        if local.time() < BACKUP_AFTER:
            return None
        dest = Path(self._settings.backup_dir).expanduser()
        if backup_service.backup_path(dest, local.date()).exists():
            return None
        source = backup_service.database_file()
        if source is None:
            return None
        try:
            result = backup_service.run_backup(
                source, dest, keep=self._settings.backup_keep, day=local.date()
            )
        except Exception:  # noqa: BLE001 - a failed backup must not stop the tick
            logger.exception("Daily database backup failed.")
            return None
        return str(result["path"]) if result["created"] else None

    # A tick later than its interval plus this is treated as a pause (sleep).
    PAUSE_GRACE_SECONDS = 120

    def _note_pause(self) -> Optional[int]:
        """Seconds since the last tick when that is well past the expected
        interval (the Mac was probably asleep), else None. Logged on wake; the
        live board's rows already show their age."""
        now_wall = self._wall()
        last, expected = self._last_tick_wall, self._expected_gap
        self._last_tick_wall = now_wall
        self._expected_gap = float(self.next_interval(self._clock()))
        if last is None or expected is None:
            return None
        elapsed = now_wall - last
        if elapsed <= expected + self.PAUSE_GRACE_SECONDS:
            return None
        logger.warning(
            "Scheduler was paused for %s min (the Mac was probably asleep); resuming. "
            "Live board rows show how old their values are.",
            int(elapsed // 60),
        )
        return int(elapsed)

    def live_refresh_seconds(self) -> int:
        return int(business_config.effective("live_refresh_seconds"))

    def next_interval(self, now: datetime.datetime) -> int:
        """Seconds to the next tick: the live-board cadence in market hours, else the alert interval."""
        if is_market_hours(now) or not self._settings.scheduler_market_hours_only:
            return max(15, self.live_refresh_seconds())
        return max(15, int(self._settings.alert_eval_interval_seconds))

    def sweep_warning(self, polled: int, swept: int) -> Optional[str]:
        """Why a full sweep cannot finish within live_refresh_seconds, if it can't.

        At the configured gap between NSE requests a sweep of N symbols takes
        about N × gap seconds. The board shows this instead of silently capping.
        """
        refresh = self.live_refresh_seconds()
        gap = float(self._settings.nse_rate_limit_delay_seconds)
        estimate = polled * gap
        if swept < polled:
            return (
                f"Sweep capped at {swept} of {polled} symbols per tick "
                f"(scheduler_max_symbols_per_tick); a full pass takes several ticks."
            )
        if estimate > refresh:
            return (
                f"A full sweep of {polled} symbols takes about {estimate:.0f} s at the "
                f"{gap:g} s gap, longer than live_refresh_seconds ({refresh} s)."
            )
        return None

    async def _refresh_market_data(self, db: Session, now: datetime.datetime) -> int:
        """Sweep held ∪ lent symbols through the quote API (Phase 2.7 / 2.13).

        One quote request per symbol and no fallback request inside a sweep. A
        403 stops the sweep and starts a 15-minute backoff; any other failure
        skips only that symbol. Trade events found by the quote service are
        kept for broadcast. Records ``sweep_status``. Returns the count refreshed.
        """
        if self._settings.scheduler_batch_series_enabled:
            return await self._refresh_by_month(db, now)
        now_utc = now.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        polled = PortfolioService.polled_symbols(db, now=now_utc)
        symbols = self._next_batch(polled)
        warning = self.sweep_warning(len(polled), len(symbols))
        if warning:
            logger.warning("Live board sweep can't finish in time: %s", warning)
        service = NSEService(db, client=self._nse_client)
        today = now.astimezone(IST).date()
        started = time.monotonic()
        self._trade_events = []
        refreshed = 0
        for symbol in symbols:
            try:
                quote = await service.get_slb_quote(symbol, fallback=False, today=today, now=now_utc)
                # Phase 2.12: on the SLB board iff NSE returned any series.
                PortfolioService.record_slb_check(db, symbol, bool(quote["series"]), now_utc)
                self._trade_events.extend(quote.get("trade_events") or [])
                refreshed += 1
            except NSEBlockedError as exc:
                self._backoff_until = now + BLOCK_BACKOFF
                logger.warning(
                    "NSE blocked the sweep at %s (%s); no NSE calls until %s.",
                    symbol, exc, self._backoff_until.isoformat(),
                )
                break
            except Exception as exc:  # noqa: BLE001 - isolate one symbol's failure
                logger.warning("Scheduler NSE refresh failed for %s: %s", symbol, exc)
        self.sweep_status = {
            "at": now_utc.isoformat(),
            "symbols": len(polled),
            "fetched": refreshed,
            "duration_seconds": round(time.monotonic() - started, 1),
            "refresh_seconds": self.live_refresh_seconds(),
            "warning": warning,
        }
        return refreshed

    async def _refresh_by_month(self, db: Session, now: datetime.datetime) -> int:
        """Read each regular B month once, retaining only held/lent securities.

        Twelve upstream calls replace ~50 per-stock calls. Every call shares
        the same two-second limiter with archive imports and stock details.
        The scheduling cadence counts from tick start, not completion.
        """
        started=time.monotonic()
        now_utc=now.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        held=set(PortfolioService.polled_symbols(db, now=now_utc, include_ineligible=True))
        seen=set();self._trade_events=[];options=[];failures=[];missing_prices=set();priced=set()
        svc=NSEService(db,client=self._nse_client)
        try:
            if self._month_options is None or self._month_options_at is None or now-self._month_options_at>datetime.timedelta(hours=6):
                master=await self._nse_client.fetch_series_master()
                self._month_options=[item for item in master.get('data',{}).get('Series B',[]) if series_family(str(item.get('key','')))=='B']
                self._month_options_at=now
            for option in self._month_options:
                if self._backoff_until and self._clock()<self._backoff_until: break
                code,label=option['key'],option['value']
                calendar=settlement_date_for_label(label)
                if not calendar: continue
                settlement,estimated=calendar
                if settlement<now.astimezone(IST).date():continue
                options.append({'code':code,'label':label,'settlement_date':settlement.isoformat()})
                try:
                    raw=await self._nse_client.fetch_slb_by_series(code)
                except NSEBlockedError: raise
                except NSEConnectionError:
                    failures.append('NSE connection unavailable');break
                except Exception as exc:
                    if not isinstance(exc,NSEError): raise
                    failures.append(code);continue
                if not isinstance(raw,dict) or not isinstance(raw.get('data'),list):
                    failures.append(code);continue
                observed=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                # The exchange timestamp belongs to this month feed, not sweep start.
                if raw.get('timestamp'):
                    try: observed=datetime.datetime.strptime(raw['timestamp'],'%d-%b-%Y %H:%M:%S').replace(tzinfo=IST).astimezone(datetime.timezone.utc).replace(tzinfo=None)
                    except (ValueError,TypeError): pass
                items=[]
                for row in raw['data']:
                    symbol=str(row.get('symbol') or '').strip().upper()
                    if symbol not in held: continue
                    item=NSEDataParser.parse_raw_slb_item({**row,'series':label})
                    if item is None:continue
                    item=dataclasses.replace(item,series_code=code,series_family='B',expiry_date=settlement,
                        settlement_date=settlement,settlement_estimated=estimated,timestamp=observed,
                        corporate_action_ex_date=NSEDataParser.parse_nse_date(row.get("caExpDate")))
                    self._trade_events.extend(svc._record_trade_events(symbol,[_row_from_market_data(item)],observed))
                    items.append(item);seen.add(symbol)
                    (priced if item.underlying_price>0 else missing_prices).add(symbol)
                svc._persist_snapshots(items,source='NSE_SERIES_B')
            from slbm_tracker.services.equity_quote_service import refresh_missing
            try:
                if 'NSE connection unavailable' not in failures: await refresh_missing(self._nse_client, missing_prices-priced)
            except NSEBlockedError: raise
            except NSEError: failures.append('underlying equity prices')
        except NSEBlockedError:
            self._backoff_until=self._clock()+BLOCK_BACKOFF
            failures.append('NSE restricted access; backing off for 15 minutes')
        for symbol in seen: PortfolioService.record_slb_check(db,symbol,True,now_utc)
        self.sweep_status={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'symbols':len(held),'fetched':len(seen),'duration_seconds':round(time.monotonic()-started,1),
            'refresh_seconds':self.live_refresh_seconds(),'mode':'holdings_by_series',
            'expiries':options,'warning':('Some months could not refresh: '+', '.join(failures)) if failures else None}
        return len(seen)

    @staticmethod
    def build_market_trade_message(event: dict[str, Any]) -> dict[str, Any]:
        """``market.trade`` frame: a traded-quantity rise since the last sweep.

        Inferred from NSE's cumulative traded quantity (no trade tape exists);
        ``inferred`` says so for the UI.
        """
        settlement = event.get("settlement_date")
        return {
            "type": "market.trade",
            "inferred": True,
            "symbol": event["symbol"],
            "series_code": event["series_code"],
            "series_family": event.get("series_family"),
            "series": event.get("series"),
            "settlement_date": settlement.isoformat() if settlement else None,
            "quantity": event["quantity"],
            "last_fee": event["last_fee"],
            "traded_quantity_total": event["traded_quantity_total"],
            "occurred_at": event["occurred_at"].isoformat(),
        }

    def _tick_sync(self, db: Session) -> tuple[int, list[int], int, int]:
        """Synchronous evaluate -> deliver -> prune body, run in a worker thread.

        ``AlertDeliveryService.deliver`` may open a *blocking* ``smtplib.SMTP``
        connection (up to the request timeout, ~10s), and evaluate/prune are
        plain synchronous DB work. This runs off the event loop via
        ``run_in_executor`` so a slow or unreachable SMTP server stalls only
        this worker thread — never the loop serving ``/api/ws`` frames and HTTP.
        The session is created with ``check_same_thread=False`` (both prod and
        test engines), so the loop thread handing it here for sequential use is
        safe. Returns ``(evaluated_count, fired_ids, pruned_count)``.
        """
        # Phase 4.2: settle contracts whose reverse leg has passed (P&L once).
        auto_closed = len(ContractService.close_expired_contracts(db, today=today_ist()))
        results = AlertService.evaluate_all(db=db)
        fired_ids = [r.alert_id for r in results if r.fired_now]
        # Mirror routes/alerts.py exactly: deliver only the newly-fired ids.
        AlertDeliveryService.deliver(db=db, alert_ids=fired_ids)
        # Bound unbounded snapshot growth from continuous auto-ingestion. The
        # DELETE is a cheap no-op until rows cross the horizon. Retention touches
        # only raw market snapshots (never alerts / money), so it is independent
        # of what fired this tick.
        pruned = SnapshotRetentionService.prune(
            db, retention_days=self._settings.snapshot_retention_days
        )
        return len(results), fired_ids, pruned, auto_closed

    async def run_one_tick(self, *, broadcast: bool = True) -> dict[str, Any]:
        """Execute exactly one evaluate cycle. Unit-testable: no sleeping.

        Opens and closes its own DB session. Catches ``Exception`` (never
        ``BaseException``) so a failure is isolated and reported in the summary
        rather than raised; ``CancelledError`` propagates.
        """
        now = self._clock()
        paused = self._note_pause()
        summary: dict[str, Any] = {
            "paused_seconds": paused,
            "backup": None,
            "evaluated": 0,
            "fired": 0,
            "market_refreshed": 0,
            "pruned": 0,
            "at": datetime.datetime.utcnow().isoformat(),
            "market_open": is_market_hours(now),
            "nse_blocked": False,
            "nse_backoff_until": None,
            "sweep": None,
            "trade_events": 0,
            "auto_closed": 0,
            "ok": True,
        }
        self._trade_events: list[dict[str, Any]] = []
        db = SessionLocal()
        try:
            gate_ok = summary["market_open"] or not self._settings.scheduler_market_hours_only
            backing_off = self._backoff_until is not None and now < self._backoff_until
            if self._settings.scheduler_refresh_market_data and gate_ok and not backing_off:
                self._backoff_until = None
                summary["market_refreshed"] = await self._refresh_market_data(db, now)
                summary["sweep"] = self.sweep_status
                summary["trade_events"] = len(self._trade_events)
            if self._backoff_until is not None and now < self._backoff_until:
                summary["nse_blocked"] = True
                summary["nse_backoff_until"] = self._backoff_until.isoformat()

            # Offload the synchronous DB + (potentially blocking) SMTP-delivery +
            # prune body to a worker thread so it can never stall the event loop.
            # The WS broadcasts below stay on the loop. See _tick_sync.
            loop = asyncio.get_running_loop()
            evaluated, fired_ids, pruned, auto_closed = await loop.run_in_executor(
                None, self._tick_sync, db
            )
            summary["auto_closed"] = auto_closed
            summary["backup"] = await loop.run_in_executor(None, self._maybe_backup, now)
            summary["evaluated"] = evaluated
            summary["fired"] = len(fired_ids)
            summary["pruned"] = pruned

            if broadcast:
                for event in self._trade_events:
                    await ws_manager.broadcast(self.build_market_trade_message(event))
                if fired_ids:
                    rows = db.query(Alert).filter(Alert.id.in_(fired_ids)).all()
                    for alert in rows:
                        await ws_manager.broadcast(self.build_alert_fired_message(alert))
                await ws_manager.broadcast(
                    {
                        "type": "scheduler_tick",
                        "evaluated": summary["evaluated"],
                        "fired": summary["fired"],
                        "market_refreshed": summary["market_refreshed"],
                        "at": summary["at"],
                    }
                )
        except Exception as exc:  # noqa: BLE001 - one bad tick must never kill the loop
            logger.exception("Alert scheduler tick failed.")
            summary["ok"] = False
            summary["error"] = str(exc)
        finally:
            db.close()
        self.last_summary=dict(summary)
        return summary

    async def _loop(self) -> None:
        """Sleep the startup grace, then tick forever until cancelled/stopped."""
        delay = max(0, int(self._settings.alert_scheduler_startup_delay_seconds))
        if delay:
            await asyncio.sleep(delay)
        while self._running:
            # run_one_tick swallows its own Exceptions; CancelledError (shutdown)
            # propagates out and ends the loop.
            tick_started=time.monotonic()
            await self.run_one_tick()
            await asyncio.sleep(max(1, self.next_interval(self._clock())-(time.monotonic()-tick_started)))

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Schedule the background loop on the given running event loop."""
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = loop.create_task(self._loop())
        logger.info(
            "Alert scheduler started (interval=%ss, startup_delay=%ss, refresh=%s, cap=%s, market_hours_only=%s).",
            self._settings.alert_eval_interval_seconds,
            self._settings.alert_scheduler_startup_delay_seconds,
            self._settings.scheduler_refresh_market_data,
            self._settings.scheduler_max_symbols_per_tick,
            self._settings.scheduler_market_hours_only,
        )

    async def stop(self) -> None:
        """Cancel the loop, await its exit, and close the long-lived NSE client."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - defensive: log, never raise on shutdown
                logger.exception("Alert scheduler task ended with an error.")
            self._task = None
        await self._nse_client.close()
        logger.info("Alert scheduler stopped.")
