"""NSE HTTP Client with session management, rate limiting, and exponential retries."""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Any, Optional
import httpx

from slbm_tracker.config import Settings, get_settings

logger = logging.getLogger(__name__)


class NSEError(Exception):
    """Base exception for NSE client operations."""


class NSEConnectionError(NSEError):
    """Raised on connection timeout or network level failure."""


class NSERateLimitError(NSEError):
    """Raised when NSE returns HTTP 429 Too Many Requests."""


class NSEBlockedError(NSEError):
    """Raised when NSE returns HTTP 403 Forbidden (bot detection / IP block)."""


class NSEServerError(NSEError):
    """Raised when NSE returns HTTP 5xx Server Error."""


class NSEClient:
    """
    Dedicated client for interacting with NSE India endpoints.
    Handles cookie warming, realistic browser headers, rate limiting, and backoff retries.
    """

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        settings: Optional[Settings] = None,
        custom_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._custom_client = custom_client
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request_time: float = 0.0
        self._request_lock = asyncio.Lock()
        self._equity_metadata = {}
        self._session_warmed: bool = False

    @property
    def headers(self) -> dict[str, str]:
        """Browser headers mimicking an active user on the NSE SLB market data page."""
        return {
            "User-Agent": self.DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": self.settings.nse_slb_page_url,
            "Origin": self.settings.nse_base_url,
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    async def get_client(self) -> httpx.AsyncClient:
        """Provide or initialize the underlying httpx client."""
        if self._custom_client is not None:
            return self._custom_client
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=httpx.Timeout(self.settings.nse_request_timeout_seconds),
                follow_redirects=True,
                http2=False,
            )
            self._session_warmed = False
        return self._client

    async def close(self) -> None:
        """Close the active HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            self._session_warmed = False

    async def _rate_limit(self) -> None:
        """Ensure minimum configured interval between consecutive requests to avoid bans."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        needed = self.settings.nse_rate_limit_delay_seconds
        if elapsed < needed:
            wait_time = needed - elapsed
            logger.debug("Rate limiting: waiting %.2fs before next NSE request", wait_time)
            await asyncio.sleep(wait_time)
        self._last_request_time = time.monotonic()

    async def warm_session(self) -> None:
        """Visit the main SLB market data page to acquire Akamai and session cookies."""
        client = await self.get_client()
        logger.info("Warming NSE session at %s", self.settings.nse_slb_page_url)
        page_headers = dict(self.headers)
        page_headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

        try:
            res = await client.get(self.settings.nse_slb_page_url, headers=page_headers)
            if res.status_code == 200:
                self._session_warmed = True
                logger.info("Session warmed successfully, cookies acquired: %s", list(client.cookies.keys()))
            else:
                logger.warning("Session warming returned status %s: %s", res.status_code, res.text[:100])
        except Exception as e:
            logger.error("Failed to warm NSE session: %s", e)
            # Do not raise immediately; request method will retry

    async def _perform_request(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        retries: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Execute an authenticated GET request with exponential backoff retries.
        """
        max_retries = retries if retries is not None else self.settings.nse_max_retries
        client = await self.get_client()

        if not self._session_warmed:
            await self.warm_session()

        attempt = 0
        backoff = 1.0

        while attempt <= max_retries:
            attempt += 1
            await self._rate_limit()

            try:
                logger.debug("NSE GET %s with params %s (attempt %s/%s)", url, params, attempt, max_retries + 1)
                response = await client.get(url, params=params, headers=self.headers)

                if response.status_code == 200:
                    try:
                        return response.json()
                    except Exception as json_err:
                        raise NSEError(f"Failed to parse JSON response from {url}: {json_err}") from json_err

                elif response.status_code == 403:
                    # A 403 is a block (house rule 9): raise at once, with no
                    # retry and no session re-warm, so the caller can stop its
                    # sweep and back off instead of hammering NSE.
                    logger.warning("NSE returned 403 Forbidden for %s; not retrying.", url)
                    raise NSEBlockedError(
                        f"NSE blocked request (403 Forbidden) to {url}. Possible bot-detection trigger."
                    )

                elif response.status_code == 429:
                    logger.warning("NSE rate limited (429). Backing off %.2fs", backoff * 2)
                    if attempt > max_retries:
                        raise NSERateLimitError(f"NSE rate limit exceeded (429) on {url}.")
                    await asyncio.sleep(backoff * 2)

                elif 500 <= response.status_code < 600:
                    logger.warning("NSE server error (%s). Backing off %.2fs", response.status_code, backoff)
                    if attempt > max_retries:
                        raise NSEServerError(f"NSE server error ({response.status_code}) on {url}.")
                    await asyncio.sleep(backoff)

                else:
                    raise NSEError(f"Unexpected response from NSE {url}: {response.status_code} {response.text[:150]}")

            except httpx.RequestError as req_err:
                # httpx.RequestError is the base for all transport-level failures:
                # TimeoutException, NetworkError (connect/read/write), ProxyError,
                # ProtocolError, DecodingError, TooManyRedirects, etc. Catching the
                # whole family (rather than only Timeout/Network) ensures every
                # transport failure is converted into NSEConnectionError so the
                # service layer's cached fallback runs instead of a raw 500. Our own
                # NSE* exceptions (403/429/5xx) are not httpx errors and still
                # propagate; programming errors (AttributeError, etc.) are not
                # caught here.
                logger.warning(
                    "Transport failure connecting to NSE: %s: %s (attempt %s)",
                    type(req_err).__name__,
                    req_err,
                    attempt,
                )
                if attempt > max_retries:
                    raise NSEConnectionError(
                        f"NSE network connection failed after {attempt} attempts: {req_err}"
                    ) from req_err
                await asyncio.sleep(backoff)

            backoff *= 2.0

        raise NSEError(f"Request to {url} failed after {max_retries} retries.")

    async def request(self, url: str, params=None, retries=None):
        async with self._request_lock:
            return await self._perform_request(url, params, retries)

    async def request_text(self, url: str):
        """Published archives: one rate-limited attempt, 404 is explicitly unavailable."""
        async with self._request_lock:
            client=await self.get_client()
            await self._rate_limit()
            try:
                result=await client.get(url,headers=self.headers)
            except httpx.RequestError as exc:
                raise NSEConnectionError("NSE archive transport failure") from exc
            if result.status_code==404: return None
            if result.status_code==403: raise NSEBlockedError("NSE archive access restricted (403)")
            if result.status_code!=200: raise NSEError(f"NSE archive HTTP {result.status_code}")
            return result.text

    async def fetch_slb_by_symbol(self, symbol: str) -> dict[str, Any]:
        """Fetch SLB contract details for a specific stock across all active series."""
        clean_sym = symbol.strip().upper()
        # Endpoint: /api/live-analysis-slb?symbol={symbol}
        return await self.request(self.settings.nse_slb_api_url, params={"symbol": clean_sym})

    async def fetch_slb_by_series(self, series: str) -> dict[str, Any]:
        """Fetch all SLB stocks for a given contract series month."""
        # Endpoint: /api/live-analysis-slb?series={series}
        return await self.request(self.settings.nse_slb_api_url, params={"series": series})

    async def fetch_etf_quotes(self) -> dict[str, Any]:
        """Official NSE ETF market-watch feed; ltP is the traded equity price."""
        return await self.request(f"{self.settings.nse_base_url}/api/etf")

    async def fetch_equity_quote(self, symbol: str) -> dict[str, Any]:
        """Use the active equity series from NSE metadata, never assume EQ."""
        symbol=symbol.strip().upper()
        if symbol not in self._equity_metadata:
            meta=await self.request(self.settings.nse_quote_api_url,
                params={"functionName":"getMetaData","symbol":symbol})
            if not isinstance(meta,dict) or not meta.get("activeSeries"):
                raise NSEError("NSE did not supply an active equity series")
            self._equity_metadata[symbol]=meta
        meta=self._equity_metadata[symbol]
        series="EQ" if "EQ" in meta["activeSeries"] else meta["activeSeries"][0]
        return await self.request(self.settings.nse_quote_api_url,params={
            "functionName":"getSymbolData","symbol":symbol,"series":series,"marketType":meta.get("marketType","N")})

    async def fetch_slb_quote(self, symbol: str) -> dict[str, Any]:
        """Per-stock SLB quote for every series (NSE's /get-quote/slb page).

        Response: ``{"data": [...one row per series...], "timestamp": [...],
        "outPosDate": [...]}``. Each row carries the series code, month label
        (``timeStamp``), ``revLegSettDt``, 5-level depth and traded quantity.
        """
        return await self.request(
            self.settings.nse_quote_api_url,
            params={
                "functionName": "getGetQuoteSLBMData",
                "symbol": symbol.strip().upper(),
                "series": "",
                "month": "",
            },
        )

    async def fetch_slb_history(
        self, symbol: str, from_date: datetime.date, to_date: datetime.date
    ) -> list[dict[str, Any]]:
        """Daily SLB trade history (OHLC fee per series); NSE keeps about two months."""
        data = await self.request(
            self.settings.nse_quote_api_url,
            params={
                "functionName": "getSLBHistoricalData",
                "symbol": symbol.strip().upper(),
                "fromDate": from_date.strftime("%d-%m-%Y"),
                "toDate": to_date.strftime("%d-%m-%Y"),
                "csv": "false",
            },
        )
        return data if isinstance(data, list) else []

    async def fetch_series_master(self) -> dict[str, Any]:
        """Fetch the list of active SLB contract months from master endpoint."""
        url = f"{self.settings.nse_base_url}/api/live-analysis-slb-series-master"
        return await self.request(url)

    async def fetch_eligible_securities(self) -> list[str]:
        """Fetch the list of securities eligible for SLB."""
        url = f"{self.settings.nse_base_url}/api/quote/suggest/equity/slb"
        try:
            data = await self.request(url)
            if isinstance(data, list):
                return [str(s).upper() for s in data if s]
            elif isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                return [str(s).upper() for s in data["data"] if s]
            return []
        except Exception as e:
            logger.warning("Failed to fetch eligible securities list: %s", e)
            return []

    async def fetch_equity_history(
        self,
        symbol: str,
        from_date: datetime.date,
        to_date: datetime.date,
        series: str = "EQ",
    ) -> dict[str, Any]:
        """Fetch security-wise historical price/volume for one equity (Phase 5B).

        NSE's historical endpoint caps the date range per request, so callers
        (EquityHistoryService) split a long window into adjacent chunks. Dates
        are sent in NSE's ``dd-mm-yyyy`` format and the series is passed as the
        JSON-array string NSE expects (e.g. ``["EQ"]``). The exact path lives in
        ``settings.nse_historical_url`` because NSE changes it over time.
        """
        clean_sym = symbol.strip().upper()
        params = {
            "symbol": clean_sym,
            "series": f'["{series}"]',
            "from": from_date.strftime("%d-%m-%Y"),
            "to": to_date.strftime("%d-%m-%Y"),
        }
        return await self.request(self.settings.nse_historical_url, params=params)
