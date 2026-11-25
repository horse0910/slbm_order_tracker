"""Unit tests for NSEClient with mocked HTTP responses."""

import datetime
import json
from pathlib import Path

import pytest
import httpx

from slbm_tracker.config import Settings
from slbm_tracker.services.nse_client import (
    NSEBlockedError,
    NSEClient,
    NSEConnectionError,
    NSERateLimitError,
    NSEServerError,
)


@pytest.fixture
def mock_settings():
    return Settings(
        nse_base_url="https://test.nseindia.com",
        nse_slb_api_url="https://test.nseindia.com/api/slb-data",
        nse_slb_page_url="https://test.nseindia.com/market-data/slb",
        nse_quote_api_url="https://test.nseindia.com/api/NextApi/apiClient/GetQuoteApi",
        nse_request_timeout_seconds=2.0,
        nse_max_retries=2,
        nse_rate_limit_delay_seconds=0.01,  # Fast for tests
    )


@pytest.mark.asyncio
async def test_nse_client_successful_request(mock_settings, sample_nse_json):
    def handler(request: httpx.Request) -> httpx.Response:
        if "market-data/slb" in str(request.url):
            return httpx.Response(200, headers={"set-cookie": "test_cookie=123"}, text="<html>OK</html>")
        if "api/slb-data" in str(request.url):
            return httpx.Response(200, json=sample_nse_json)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as custom_http:
        client = NSEClient(settings=mock_settings, custom_client=custom_http)
        data = await client.fetch_slb_by_symbol("RELIANCE")
        assert "data" in data
        assert len(data["data"]) == 2
        assert client._session_warmed is True


@pytest.mark.asyncio
async def test_nse_client_retries_on_500_then_succeeds(mock_settings, sample_nse_json):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        if "market-data/slb" in str(request.url):
            return httpx.Response(200, text="OK")
        if "api/slb-data" in str(request.url):
            call_count += 1
            if call_count == 1:
                return httpx.Response(502, text="Bad Gateway")
            return httpx.Response(200, json=sample_nse_json)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as custom_http:
        client = NSEClient(settings=mock_settings, custom_client=custom_http)
        data = await client.fetch_slb_by_symbol("RELIANCE")
        assert call_count == 2
        assert len(data["data"]) == 2


@pytest.mark.asyncio
async def test_nse_client_raises_blocked_on_first_403_without_retrying(mock_settings):
    """House rule 9: a 403 is a block. Raise at once: no retry, no session re-warm."""
    calls = {"page": 0, "api": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "market-data/slb" in str(request.url):
            calls["page"] += 1
            return httpx.Response(200, text="OK")
        calls["api"] += 1
        return httpx.Response(403, text="Forbidden / Bot Detected")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as custom_http:
        client = NSEClient(settings=mock_settings, custom_client=custom_http)
        with pytest.raises(NSEBlockedError, match="403 Forbidden"):
            await client.fetch_slb_by_symbol("INFY")
    assert calls == {"page": 1, "api": 1}


@pytest.mark.asyncio
async def test_nse_client_raises_rate_limit_on_429(mock_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if "market-data/slb" in str(request.url):
            return httpx.Response(200, text="OK")
        return httpx.Response(429, text="Too Many Requests")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as custom_http:
        client = NSEClient(settings=mock_settings, custom_client=custom_http)
        with pytest.raises(NSERateLimitError, match="429"):
            await client.fetch_slb_by_symbol("TCS")


@pytest.mark.asyncio
async def test_nse_client_raises_connection_error_on_timeout(mock_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if "market-data/slb" in str(request.url):
            return httpx.Response(200, text="OK")
        raise httpx.ReadTimeout("Connection timed out")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as custom_http:
        client = NSEClient(settings=mock_settings, custom_client=custom_http)
        with pytest.raises(NSEConnectionError, match="network connection failed"):
            await client.fetch_slb_by_symbol("WIPRO")


@pytest.mark.asyncio
async def test_nse_client_wraps_non_network_transport_error(mock_settings):
    """Regression: a transport failure that is NOT a Timeout/NetworkError (e.g.
    httpx.ProxyError, a RequestError subclass) must be converted into the
    NSEConnectionError abstraction rather than escaping as a raw httpx error
    (which previously bubbled up to an HTTP 500 past the service fallback)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "market-data/slb" in str(request.url):
            return httpx.Response(200, text="OK")
        raise httpx.ProxyError("407 Proxy blocked", request=request)

    # ProxyError is a RequestError/TransportError but NOT an httpx.NetworkError,
    # so it exercises the widened except clause.
    assert issubclass(httpx.ProxyError, httpx.RequestError)
    assert not issubclass(httpx.ProxyError, httpx.NetworkError)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as custom_http:
        client = NSEClient(settings=mock_settings, custom_client=custom_http)
        with pytest.raises(NSEConnectionError, match="network connection failed"):
            await client.fetch_slb_by_symbol("HDFCBANK")


FIXTURES = Path(__file__).parent / "fixtures" / "nse"


@pytest.mark.asyncio
async def test_fetch_slb_quote_calls_getquote_api_with_symbol(mock_settings):
    payload = json.loads((FIXTURES / "getquote_slbm_IRFC.json").read_text())
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "market-data/slb" in str(request.url):
            return httpx.Response(200, text="OK")
        seen.append(request.url)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as custom_http:
        client = NSEClient(settings=mock_settings, custom_client=custom_http)
        data = await client.fetch_slb_quote(" irfc ")
    assert len(data["data"]) == 24
    url = seen[0]
    assert str(url).startswith(mock_settings.nse_quote_api_url)
    assert url.params["functionName"] == "getGetQuoteSLBMData"
    assert url.params["symbol"] == "IRFC"


@pytest.mark.asyncio
async def test_fetch_slb_history_sends_nse_dates_and_returns_rows(mock_settings):
    payload = json.loads((FIXTURES / "slb_history_IRFC.json").read_text())
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "market-data/slb" in str(request.url):
            return httpx.Response(200, text="OK")
        seen.append(request.url)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as custom_http:
        client = NSEClient(settings=mock_settings, custom_client=custom_http)
        rows = await client.fetch_slb_history("IRFC", datetime.date(2026, 9, 1), datetime.date(2026, 10, 4))
    assert len(rows) == 70
    assert seen[0].params["functionName"] == "getSLBHistoricalData"
    assert seen[0].params["fromDate"] == "01-09-2026"
    assert seen[0].params["toDate"] == "04-10-2026"


@pytest.mark.asyncio
async def test_fetch_slb_history_non_list_payload_is_empty(mock_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if "market-data/slb" in str(request.url):
            return httpx.Response(200, text="OK")
        return httpx.Response(200, json={"error": "no data"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as custom_http:
        client = NSEClient(settings=mock_settings, custom_client=custom_http)
        assert await client.fetch_slb_history("IRFC", datetime.date(2026, 9, 1), datetime.date(2026, 10, 4)) == []
