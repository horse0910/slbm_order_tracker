# SLBM Order Tracking

A personal tool for lending shares on NSE's Securities Lending and Borrowing Mechanism (SLBM) through Zerodha.
It watches the NSE SLB board for your holdings, finds the **Series B** bids Zerodha accepts, prices them after
Zerodha's charges, alerts you, and tracks lent contracts through settlement and rollover. It is decision support
only: it never places orders.

It runs on your Mac only, bound to `127.0.0.1` (no server, no login).

## Folders

- This folder stays on `main` and is the one you run. Never switch branches here.
- `../SLBM Order Tracking-dev` is the development worktree (feature branches, its own `.venv` and `node_modules`).

## Run it

Take a backup with a new name before the first start after an update; startup applies any new columns:

```bash
sqlite3 slbm_tracker.db ".backup slbm_tracker.$(date +%F).backup.db"
```

**One address, the built app on 8010** (what the launch agent runs):

```bash
npm --prefix frontend run build
.venv/bin/python -m uvicorn slbm_tracker.api.app:app --host 127.0.0.1 --port 8010
```

Open http://127.0.0.1:8010 (`/live`, `/slb/IRFC` and other pages load on refresh).

**Or the dev servers** (frontend with hot reload on 5180):

```bash
.venv/bin/python -m uvicorn slbm_tracker.api.app:app --host 127.0.0.1 --port 8010
VITE_BACKEND_TARGET=http://127.0.0.1:8010 npm --prefix frontend run dev -- --port 5180 --strictPort
```

## Start at login (launch agent)

```bash
npm --prefix frontend run build
scripts/install_launch_agent.sh
```

It installs `~/Library/LaunchAgents/com.harsh.slbm-tracker.plist`, which runs this folder's `.venv` uvicorn on
127.0.0.1:8010, restarts it if it exits, and writes logs to `~/Library/Logs/slbm-tracker/`. Stop anything already
on 8010 first. Check it with `curl http://127.0.0.1:8010/api/health`. `scripts/install_launch_agent.sh --print`
shows the plist without installing it. Remove it with:

```bash
scripts/uninstall_launch_agent.sh
```

After updating `main`, rebuild the frontend and restart the agent:

```bash
launchctl kickstart -k gui/$(id -u)/com.harsh.slbm-tracker
```

## Keep the Mac awake in market hours

Polling stops while the Mac sleeps: no sweeps, no trade events, no alerts. The NSE SLB session runs 09:00–17:15
IST, Monday to Friday.

1. Wake the Mac just before the open every weekday (needs your password; run it yourself):

   ```bash
   sudo pmset repeat wakeorpoweron MTWRF 08:55:00
   ```

   Check it with `pmset -g sched`; clear it with `sudo pmset repeat cancel`.
2. On power, stop the Mac sleeping when the display turns off: System Settings → Battery (or Energy) → Options →
   "Prevent automatic sleeping on power adapter when the display is off".
3. Lid closed counts as sleep unless an external display is attached.

If the Mac does sleep, the scheduler logs how long it was paused when it wakes ("Scheduler was paused for N min")
and the live board shows each row's age, so stale values are visible rather than hidden.

## Backups

Every day, on the first scheduler tick after 17:20 IST, the app backs the database up with SQLite's online backup
(WAL included) to `~/Library/Application Support/slbm-tracker/backups/slbm_tracker-YYYY-MM-DD.db`, checks it
(integrity and row counts) and keeps the newest 14. Change it with `BACKUPS_ENABLED`, `BACKUP_DIR` and `BACKUP_KEEP`
in `.env`. To restore one, stop the app and run:

```bash
sqlite3 slbm_tracker.db ".restore '$HOME/Library/Application Support/slbm-tracker/backups/slbm_tracker-YYYY-MM-DD.db'"
```

## Notifications

- macOS: on by default (Settings → Notifications); macOS may ask once to allow notifications from Script Editor.
- WhatsApp (opportunity and rollover alerts only): set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `WHATSAPP_FROM`
  and `WHATSAPP_TO` in `.env`. The app never stores or shows them.
- Settings → "Send a test notification" checks both.

## Market-hours fixture capture (development)

Two quote captures 5 minutes apart for IRFC plus other symbols, into a new `tests/fixtures/nse/session-YYYY-MM-DD/`
folder (never touches the existing fixtures):

```bash
.venv/bin/python scripts/capture_nse_fixtures.py --session --symbols SBIN NTPC --gap-minutes 5
```

## Tests

```bash
.venv/bin/python -m pytest -q
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build   # keep under 200 KB gzipped
```

