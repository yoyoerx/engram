"""Unit tests for retry utility. No live services required."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engram_mcp.retry import (
    _is_transient,
    call_with_retry_async,
    call_with_retry_sync,
    neo4j_driver,
    retry_async,
    retry_sync,
)


# ── Transient error classifier ────────────────────────────────────────────────

# Names must match the strings in retry._TRANSIENT_* tuples exactly.
class ServiceUnavailable(Exception):
    pass

class ConnectError(Exception):
    pass

class SessionExpired(Exception):
    pass


def test_is_transient_by_class_name():
    assert _is_transient(ServiceUnavailable("down"))
    assert _is_transient(ConnectError("refused"))
    assert _is_transient(SessionExpired("expired"))


def test_is_transient_by_message_pattern():
    assert _is_transient(Exception("connection refused by host"))
    assert _is_transient(Exception("connection reset by peer"))
    assert _is_transient(Exception("read timeout after 30s"))
    assert _is_transient(Exception("connection closed unexpectedly"))


def test_is_not_transient_for_logic_errors():
    assert not _is_transient(ValueError("bad input"))
    assert not _is_transient(KeyError("missing key"))
    assert not _is_transient(Exception("syntax error in cypher query"))


# ── retry_sync decorator ──────────────────────────────────────────────────────

@patch("engram_mcp.retry.time.sleep")
def test_retry_sync_succeeds_on_second_attempt(mock_sleep):
    calls = []

    @retry_sync(max_attempts=3, base_delay=0.1, backoff=2.0)
    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise ConnectError("connection refused")
        return "ok"

    assert flaky() == "ok"
    assert len(calls) == 2
    mock_sleep.assert_called_once_with(0.1)


@patch("engram_mcp.retry.time.sleep")
def test_retry_sync_does_not_retry_non_transient(mock_sleep):
    attempts = []

    @retry_sync(max_attempts=3, base_delay=0.1)
    def always_bad():
        attempts.append(1)
        raise ValueError("permanent failure")

    with pytest.raises(ValueError, match="permanent failure"):
        always_bad()

    assert len(attempts) == 1
    mock_sleep.assert_not_called()


@patch("engram_mcp.retry.time.sleep")
def test_retry_sync_exhausts_max_attempts(mock_sleep):
    attempts = []

    @retry_sync(max_attempts=3, base_delay=0.1, backoff=2.0)
    def always_fails():
        attempts.append(1)
        raise ConnectError("connection refused")

    with pytest.raises(ConnectError):
        always_fails()

    assert len(attempts) == 3
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(0.1)
    mock_sleep.assert_any_call(0.2)


# ── retry_async decorator ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_async_succeeds_on_second_attempt():
    calls = []

    with patch("engram_mcp.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        @retry_async(max_attempts=3, base_delay=0.1, backoff=2.0)
        async def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise ConnectError("connection refused")
            return "async-ok"

        result = await flaky()

    assert result == "async-ok"
    assert len(calls) == 2
    mock_sleep.assert_awaited_once_with(0.1)


@pytest.mark.asyncio
async def test_retry_async_does_not_retry_non_transient():
    attempts = []

    with patch("engram_mcp.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        @retry_async(max_attempts=3, base_delay=0.1)
        async def always_bad():
            attempts.append(1)
            raise ValueError("permanent")

        with pytest.raises(ValueError):
            await always_bad()

    assert len(attempts) == 1
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_async_exhausts_max_attempts():
    attempts = []

    with patch("engram_mcp.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        @retry_async(max_attempts=3, base_delay=0.1, backoff=2.0)
        async def always_fails():
            attempts.append(1)
            raise ConnectError("connection refused")

        with pytest.raises(ConnectError):
            await always_fails()

    assert len(attempts) == 3
    assert mock_sleep.await_count == 2


# ── call_with_retry_sync / async ──────────────────────────────────────────────

@patch("engram_mcp.retry.time.sleep")
def test_call_with_retry_sync_functional_form(mock_sleep):
    calls = []

    def flaky(x):
        calls.append(x)
        if len(calls) < 2:
            raise ConnectError("connection refused")
        return x * 2

    result = call_with_retry_sync(flaky, 5, max_attempts=3, base_delay=0.1)
    assert result == 10
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_call_with_retry_async_functional_form():
    calls = []

    async def flaky(x):
        calls.append(x)
        if len(calls) < 2:
            raise ConnectError("connection refused")
        return x * 3

    with patch("engram_mcp.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await call_with_retry_async(flaky, 4, max_attempts=3, base_delay=0.1)

    assert result == 12
    assert len(calls) == 2


# ── neo4j_driver context manager ──────────────────────────────────────────────

@patch("engram_mcp.retry.time.sleep")
def test_neo4j_driver_connects_after_transient_failures(mock_sleep):
    attempts = []
    mock_driver = MagicMock()
    mock_driver.verify_connectivity.return_value = None

    def driver_factory(uri, auth):
        attempts.append(1)
        if len(attempts) < 3:
            raise ServiceUnavailable("neo4j unavailable")
        return mock_driver

    with patch("neo4j.GraphDatabase") as mock_gdb:
        mock_gdb.driver.side_effect = driver_factory
        with neo4j_driver("bolt://localhost:7687", ("neo4j", "pass")) as driver:
            assert driver is mock_driver

    assert len(attempts) == 3
    mock_driver.close.assert_called_once()


@patch("engram_mcp.retry.time.sleep")
def test_neo4j_driver_raises_on_non_transient(mock_sleep):
    with patch("neo4j.GraphDatabase") as mock_gdb:
        mock_gdb.driver.side_effect = ValueError("bad URI")
        with pytest.raises(ValueError, match="bad URI"):
            with neo4j_driver("bolt://localhost:7687", ("neo4j", "pass")):
                pass

    mock_sleep.assert_not_called()


@patch("engram_mcp.retry.time.sleep")
def test_neo4j_driver_closes_on_exception_in_body(mock_sleep):
    mock_driver = MagicMock()
    mock_driver.verify_connectivity.return_value = None

    with patch("neo4j.GraphDatabase") as mock_gdb:
        mock_gdb.driver.return_value = mock_driver
        with pytest.raises(RuntimeError, match="body error"):
            with neo4j_driver("bolt://localhost:7687", ("neo4j", "pass")):
                raise RuntimeError("body error")

    mock_driver.close.assert_called_once()
