"""Run this isolated, user-owned checkout; the original live database stays untouched."""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import uvicorn

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
DEFAULTS={
 'DATABASE_URL':f'sqlite:///{ROOT / "runtime" / "slbm_tracker.db"}',
 'BACKUP_DIR':str(ROOT/'runtime'/'backups'),
 'HISTORY_CACHE_DIR':str(ROOT/'runtime'/'history'),
 'FRONTEND_DIST_DIR':str(ROOT/'frontend'/'dist'),
 'SCHEDULER_BATCH_SERIES_ENABLED':'true','LIVE_REFRESH_SECONDS':'60',
 'ALERT_EVAL_INTERVAL_SECONDS':'60','ALERT_SCHEDULER_STARTUP_DELAY_SECONDS':'2',
 'NSE_MAX_RETRIES':'1','NSE_REQUEST_TIMEOUT_SECONDS':'10',
 'MACOS_NOTIFICATIONS_ENABLED':'true','NOTIFICATIONS_ENABLED':'true',
 'SNAPSHOT_RETENTION_DAYS':'365','OPPORTUNITY_QUOTE_MAX_AGE_SECONDS':'180',
}
for key,value in DEFAULTS.items():os.environ.setdefault(key,value)
class LegacyRedirect(BaseHTTPRequestHandler):
 def do_GET(self):
  path='/live' if self.path=='/' else self.path
  self.send_response(302)
  self.send_header('Location','http://127.0.0.1:8010'+path)
  self.end_headers()
 def log_message(self,*args):pass


def start_legacy_redirect():
 try:
  server=ThreadingHTTPServer(('127.0.0.1',8000),LegacyRedirect)
 except OSError:
  print('Port 8000 is already in use; the app remains available on 8010.',flush=True)
  return
 threading.Thread(target=server.serve_forever,daemon=True).start()


if __name__=='__main__':
 start_legacy_redirect()
 uvicorn.run('slbm_tracker.api.app:app',host='127.0.0.1',port=int(os.environ.get('SLBM_PORT','8010')),log_level='info')
