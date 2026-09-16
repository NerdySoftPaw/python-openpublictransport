"""HTTP failures must be distinguishable from empty results (issue #88).

Before this contract existed, every provider logged a non-200 response and
returned ``[]`` / ``None``. A Home Assistant config flow could therefore not
tell "this search found nothing" apart from "the provider is down", and showed
"no results found" during an outage.

The rules verified here:

  * 4xx fails immediately — retrying a bad key or a dead endpoint only delays
    the error the user has to see.
  * 5xx, timeouts and connection errors are retried, then raise.
  * 401/403 raise ``AuthenticationError`` (a subclass of ``ApiError``, so a
    caller that only cares about "the API failed" still catches it).
  * A 200 with an empty result set is *not* an error.
"""

import asyncio

import aiohttp
import pytest

from openpublictransport.exceptions import (
    ApiConnectionError,
    ApiError,
    ApiResponseError,
    ApiTimeoutError,
    AuthenticationError,
    OpenPublicTransportError,
)
from openpublictransport.providers.bvg import BVGProvider
from openpublictransport.providers.kvv import KVVProvider
from openpublictransport.providers.bart import BARTProvider
from openpublictransport.providers.oebb import OeBBProvider
from openpublictransport.providers.sbb import SBBProvider
from openpublictransport.providers.vbn import VBNOTPProvider, VBNTriasProvider


class _FakeResponse:
    """Minimal stand-in for an aiohttp response."""

    def __init__(self, status=200, payload=None, text="", raises=None):
        self.status = status
        self._payload = payload if payload is not None else {}
        self._text = text
        self._raises = raises

    async def json(self, content_type=None):
        if self._raises:
            raise self._raises
        return self._payload

    async def text(self):
        return self._text

    async def read(self):
        return self._text.encode("utf-8")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Counts calls so retry behaviour can be asserted."""

    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.calls = 0

    def _handle(self, url, **kwargs):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._response

    def get(self, url, **kwargs):
        return self._handle(url, **kwargs)

    def post(self, url, **kwargs):
        return self._handle(url, **kwargs)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """Keep the retry tests instant."""
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)


async def _instant_sleep(_seconds):
    return None


# ── the distinction the issue is about ────────────────────────────────────────


async def test_server_error_raises_with_status():
    session = _FakeSession(_FakeResponse(status=503))

    with pytest.raises(ApiError) as excinfo:
        await KVVProvider(session).search_stops("Marktplatz")

    assert excinfo.value.status == 503
    assert not isinstance(excinfo.value, AuthenticationError)


async def test_empty_result_is_not_an_error():
    session = _FakeSession(_FakeResponse(payload={"locations": []}))

    assert await KVVProvider(session).search_stops("Nirgendwo") == []


# ── retry policy ──────────────────────────────────────────────────────────────


async def test_server_error_is_retried_then_raises():
    session = _FakeSession(_FakeResponse(status=502))

    with pytest.raises(ApiError):
        await KVVProvider(session).fetch_departures("de:08212:1", "", "", 10)

    assert session.calls == 3


async def test_client_error_is_not_retried():
    session = _FakeSession(_FakeResponse(status=404))

    with pytest.raises(ApiError) as excinfo:
        await KVVProvider(session).fetch_departures("de:08212:1", "", "", 10)

    assert excinfo.value.status == 404
    assert session.calls == 1


async def test_authentication_error_is_not_retried():
    session = _FakeSession(_FakeResponse(status=401))

    with pytest.raises(AuthenticationError) as excinfo:
        await KVVProvider(session).fetch_departures("de:08212:1", "", "", 10)

    assert excinfo.value.status == 401
    assert isinstance(excinfo.value, ApiError)
    assert session.calls == 1


# ── transport-level failures ──────────────────────────────────────────────────


async def test_timeout_raises_api_timeout_error():
    session = _FakeSession(raises=asyncio.TimeoutError())

    with pytest.raises(ApiTimeoutError):
        await KVVProvider(session).search_stops("Marktplatz")


async def test_connection_failure_raises_api_connection_error():
    session = _FakeSession(raises=aiohttp.ClientConnectionError("no route to host"))

    with pytest.raises(ApiConnectionError):
        await KVVProvider(session).search_stops("Marktplatz")


async def test_timeout_error_is_an_open_public_transport_error():
    session = _FakeSession(raises=asyncio.TimeoutError())

    with pytest.raises(OpenPublicTransportError):
        await KVVProvider(session).search_stops("Marktplatz")


# ── payload failures ──────────────────────────────────────────────────────────


async def test_undecodable_body_raises_response_error():
    session = _FakeSession(_FakeResponse(raises=ValueError("not json")))

    with pytest.raises(ApiResponseError):
        await KVVProvider(session).search_stops("Marktplatz")


async def test_wrong_payload_shape_raises_response_error():
    session = _FakeSession(_FakeResponse(payload=["unexpected"]))

    with pytest.raises(ApiResponseError):
        await KVVProvider(session).search_stops("Marktplatz")


async def test_error_body_is_carried_on_the_exception():
    session = _FakeSession(_FakeResponse(status=500, text="quota exceeded"))

    with pytest.raises(ApiError) as excinfo:
        await KVVProvider(session).search_stops("Marktplatz")

    assert excinfo.value.body == "quota exceeded"
    assert "quota exceeded" in str(excinfo.value)


# ── the same contract across the other protocol families ──────────────────────


@pytest.mark.parametrize(
    "provider_class",
    [
        KVVProvider,
        BVGProvider,
        OeBBProvider,
        BARTProvider,
        VBNTriasProvider,
        VBNOTPProvider,
        SBBProvider,
    ],
    ids=["efa", "fptf", "hafas", "hafas-mgate", "trias", "otp", "sbb"],
)
async def test_every_protocol_family_raises_on_server_error(provider_class):
    session = _FakeSession(_FakeResponse(status=503))

    with pytest.raises(ApiError) as excinfo:
        await provider_class(session).search_stops("Hauptbahnhof")

    assert excinfo.value.status == 503
