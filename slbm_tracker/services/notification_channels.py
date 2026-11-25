"""Outbound notification channels for the Mac (Phase 5).

* macOS: ``osascript -e 'display notification ...'``, title and body escaped as
  AppleScript string literals and passed as a single argv item (no shell).
  Skipped, not failed, when not running on macOS.
* WhatsApp via Twilio (phone): enabled only when TWILIO_ACCOUNT_SID,
  TWILIO_AUTH_TOKEN, WHATSAPP_FROM and WHATSAPP_TO are all set in the
  environment. The credentials are read from settings at send time and never
  stored, logged, or returned; failure details carry only an HTTP status or
  an exception class name, never the URL (it contains the account SID).

Each sender returns ``(delivered, outcome, detail)`` with outcome ``sent``,
``skipped`` or ``failed``. ``_run`` and ``_http_post`` are module-level so
tests can replace them; the suite's conftest stubs both by default.
"""

from __future__ import annotations

import logging
import subprocess
import sys

import httpx

from slbm_tracker.config import get_settings

logger = logging.getLogger(__name__)

OUTCOME_SENT = "sent"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FAILED = "failed"

MACOS_SOUND = "Glass"
TWILIO_MESSAGES_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

Result = tuple[bool, str, str]


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, **kwargs)  # noqa: S603 - fixed argv, no shell


def _http_post(url: str, *, auth: tuple[str, str], data: dict[str, str], timeout: float) -> httpx.Response:
    with httpx.Client(timeout=timeout) as client:
        return client.post(url, auth=auth, data=data)


# ----------------------------------------------------------------------- macOS


def macos_available() -> bool:
    return sys.platform == "darwin"


def applescript_quote(text: str) -> str:
    """An AppleScript string literal: backslashes and quotes escaped, newlines flattened."""
    flat = " ".join(str(text).splitlines())
    return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


def send_macos(title: str, body: str) -> Result:
    if not macos_available():
        return False, OUTCOME_SKIPPED, "skipped: not macOS"
    script = (
        f"display notification {applescript_quote(body)} with title {applescript_quote(title)}"
        f' sound name "{MACOS_SOUND}"'
    )
    try:
        result = _run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, OUTCOME_FAILED, f"osascript error: {type(exc).__name__}"
    if result.returncode != 0:
        return False, OUTCOME_FAILED, f"osascript exit {result.returncode}: {(result.stderr or '').strip()[:200]}"
    return True, OUTCOME_SENT, "Shown as a macOS notification."


# -------------------------------------------------------------------- WhatsApp


def whatsapp_configured() -> bool:
    s = get_settings()
    return all(
        (getattr(s, name, "") or "").strip()
        for name in ("twilio_account_sid", "twilio_auth_token", "whatsapp_from", "whatsapp_to")
    )


def _whatsapp_address(number: str) -> str:
    number = number.strip()
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


def send_whatsapp(text: str) -> Result:
    if not whatsapp_configured():
        return False, OUTCOME_SKIPPED, "skipped: WhatsApp not configured"
    s = get_settings()
    sid, token = s.twilio_account_sid.strip(), s.twilio_auth_token.strip()
    try:
        response = _http_post(
            TWILIO_MESSAGES_URL.format(sid=sid),
            auth=(sid, token),
            data={
                "From": _whatsapp_address(s.whatsapp_from),
                "To": _whatsapp_address(s.whatsapp_to),
                "Body": text[:1500],
            },
            timeout=10.0,
        )
    except Exception as exc:  # noqa: BLE001 - delivery never raises; detail stays secret-free
        logger.warning("WhatsApp send failed: %s", type(exc).__name__)
        return False, OUTCOME_FAILED, f"WhatsApp send failed: {type(exc).__name__}"
    if response.status_code >= 300:
        logger.warning("WhatsApp send failed: HTTP %s", response.status_code)
        return False, OUTCOME_FAILED, f"WhatsApp send failed: HTTP {response.status_code}"
    return True, OUTCOME_SENT, "Sent on WhatsApp."
