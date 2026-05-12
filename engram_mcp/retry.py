"""Retry utility with exponential backoff for transient service failures."""

import asyncio
import functools
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import TypeVar

from engram_mcp.logger import get_logger

_log = get_logger("retry")

F = TypeVar("F")

# Exceptions considered transient for each service
_TRANSIENT_HTTP = (
    "ConnectError",
    "RemoteProtocolError",
    "ReadTimeout",
    "ConnectTimeout",
    "TimeoutException",
)

_TRANSIENT_NEO4J = (
    "ServiceUnavailable",
    "SessionExpired",
    "TransientError",
    "ConnectionResetError",
)


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__
    msg = str(exc).lower()
    transient_names = _TRANSIENT_HTTP + _TRANSIENT_NEO4J
    if name in transient_names:
        return True
    # httpx wraps some errors; check message too
    if "connection" in msg and ("refused" in msg or "reset" in msg or "closed" in msg):
        return True
    if "timeout" in msg:
        return True
    return False


def retry_sync(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Decorator: retry a sync function on transient failures with exponential backoff."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if not _is_transient(exc) or attempt == max_attempts:
                        raise
                    last_exc = exc
                    _log.warning(
                        "transient error, retrying",
                        extra={"attempt": attempt, "delay": delay, "exc": str(exc)},
                    )
                    time.sleep(delay)
                    delay *= backoff
            raise last_exc  # unreachable but satisfies type checkers
        return wrapper
    return decorator


def retry_async(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Decorator: retry an async function on transient failures with exponential backoff."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:
                    if not _is_transient(exc) or attempt == max_attempts:
                        raise
                    last_exc = exc
                    _log.warning(
                        "transient error, retrying",
                        extra={"attempt": attempt, "delay": delay, "exc": str(exc)},
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff
            raise last_exc
        return wrapper
    return decorator


async def call_with_retry_async(
    fn: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    backoff: float = 2.0,
    **kwargs,
):
    """Call an async callable with retry. Use when decorating isn't convenient."""
    delay = base_delay
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            if not _is_transient(exc) or attempt == max_attempts:
                raise
            last_exc = exc
            await asyncio.sleep(delay)
            delay *= backoff
    raise last_exc


def call_with_retry_sync(
    fn: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    backoff: float = 2.0,
    **kwargs,
):
    """Call a sync callable with retry. Use when decorating isn't convenient."""
    delay = base_delay
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not _is_transient(exc) or attempt == max_attempts:
                raise
            last_exc = exc
            time.sleep(delay)
            delay *= backoff
    raise last_exc


@contextmanager
def neo4j_driver(uri: str, auth: tuple, max_attempts: int = 3, base_delay: float = 0.5):
    """
    Context manager that yields a connected Neo4j driver, retrying on transient
    connection failures. Always closes the driver on exit.

    Usage:
        with neo4j_driver(NEO4J_URI, (NEO4J_USER, NEO4J_PASSWORD)) as driver:
            with driver.session() as session:
                session.run(...)
    """
    from neo4j import GraphDatabase  # local import to avoid hard dep at module level

    delay = base_delay
    last_exc: Exception | None = None
    driver = None
    for attempt in range(1, max_attempts + 1):
        try:
            driver = GraphDatabase.driver(uri, auth=auth)
            driver.verify_connectivity()
            break
        except Exception as exc:
            if driver:
                try:
                    driver.close()
                except Exception:
                    pass
                driver = None
            if not _is_transient(exc) or attempt == max_attempts:
                raise
            last_exc = exc
            time.sleep(delay)
            delay *= base_delay

    try:
        yield driver
    finally:
        if driver:
            try:
                driver.close()
            except Exception:
                pass
