"""
tests/test_bybit_client_time_sync.py — unit tests for BybitClient clock sync.

All tests use unittest.mock so no real network calls are made.

Test groups:

  TestRecvWindow
    - recv_window is forwarded to the pybit HTTP session

  TestTimeSyncDisabled
    - sync_time=False skips the server-time fetch entirely

  TestTimeSyncSuccess
    - Offset is applied when local clock lags the server
    - Offset is applied when local clock leads the server
    - Negligible offset (<500 ms) is silently skipped
    - Patched generator adds the correct offset

  TestTimeSyncFailures
    - Network / exception during server-time fetch → graceful fallback
    - Non-zero retCode → graceful fallback
    - Malformed response (missing key) → graceful fallback

  TestGetServerTimeMs
    - Returns correct millisecond value from timeSecond
    - Raises BybitAPIError on non-zero retCode
    - Raises BybitAPIError on network error
    - Raises BybitAPIError on malformed response
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

# We import the module under test after patching pybit so the HTTP
# constructor never actually tries to open a socket.
import pybit._helpers as _pybit_helpers
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _server_time_response(time_second: int, ret_code: int = 0) -> dict:
    return {
        "retCode": ret_code,
        "retMsg": "OK" if ret_code == 0 else "error",
        "result": {
            "timeSecond": str(time_second),
            "timeNano": str(time_second * 1_000_000_000),
        },
    }


def _make_client(
    sync_time: bool = False,
    recv_window: int = 10_000,
    server_time_response: dict | None = None,
    server_time_raises: Exception | None = None,
) -> "BybitClient":
    """
    Construct a BybitClient with pybit.HTTP fully mocked.

    The session mock's get_server_time() returns server_time_response, or
    raises server_time_raises if provided.
    """
    from api.bybit_client import BybitClient

    mock_session = MagicMock()

    if server_time_raises is not None:
        mock_session.get_server_time.side_effect = server_time_raises
    elif server_time_response is not None:
        mock_session.get_server_time.return_value = server_time_response
    else:
        mock_session.get_server_time.return_value = _server_time_response(
            int(time.time())
        )

    with patch("api.bybit_client.HTTP", return_value=mock_session):
        client = BybitClient(
            testnet=False,
            api_key="key",
            api_secret="secret",
            recv_window=recv_window,
            sync_time=sync_time,
        )

    # Expose mock for assertions
    client._mock_session = mock_session  # type: ignore[attr-defined]
    return client


# ---------------------------------------------------------------------------
# TestRecvWindow
# ---------------------------------------------------------------------------


class TestRecvWindow:
    def test_default_recv_window_is_10000(self) -> None:
        with patch("api.bybit_client.HTTP") as mock_http:
            mock_http.return_value = MagicMock()
            from api.bybit_client import BybitClient

            BybitClient(api_key="k", api_secret="s", sync_time=False)
            _, kwargs = mock_http.call_args
            assert kwargs["recv_window"] == 10_000

    def test_custom_recv_window_forwarded(self) -> None:
        with patch("api.bybit_client.HTTP") as mock_http:
            mock_http.return_value = MagicMock()
            from api.bybit_client import BybitClient

            BybitClient(
                api_key="k", api_secret="s", recv_window=20_000, sync_time=False
            )
            _, kwargs = mock_http.call_args
            assert kwargs["recv_window"] == 20_000

    def test_recv_window_5000_forwarded(self) -> None:
        with patch("api.bybit_client.HTTP") as mock_http:
            mock_http.return_value = MagicMock()
            from api.bybit_client import BybitClient

            BybitClient(api_key="k", api_secret="s", recv_window=5_000, sync_time=False)
            _, kwargs = mock_http.call_args
            assert kwargs["recv_window"] == 5_000


# ---------------------------------------------------------------------------
# TestTimeSyncDisabled
# ---------------------------------------------------------------------------


class TestTimeSyncDisabled:
    def test_server_time_not_fetched_when_sync_false(self) -> None:
        client = _make_client(sync_time=False)
        client._mock_session.get_server_time.assert_not_called()  # type: ignore[attr-defined]

    def test_timestamp_generator_unchanged_when_sync_false(self) -> None:
        original = _pybit_helpers.generate_timestamp
        _make_client(sync_time=False)
        # Generator should be the same object (or at least produce values
        # within 1 second of local time).
        ts = _pybit_helpers.generate_timestamp()
        local_ms = int(time.time() * 1_000)
        assert abs(ts - local_ms) < 1_000


# ---------------------------------------------------------------------------
# TestTimeSyncSuccess
# ---------------------------------------------------------------------------


class TestTimeSyncSuccess:
    def setup_method(self) -> None:
        """Restore the original generate_timestamp before each test."""
        self._original_gen = _pybit_helpers.generate_timestamp

    def teardown_method(self) -> None:
        """Always restore generate_timestamp after each test."""
        _pybit_helpers.generate_timestamp = self._original_gen

    def test_server_time_fetched_once_on_init(self) -> None:
        now_s = int(time.time())
        client = _make_client(
            sync_time=True,
            server_time_response=_server_time_response(now_s),
        )
        client._mock_session.get_server_time.assert_called_once()  # type: ignore[attr-defined]

    def test_large_positive_offset_patches_generator(self) -> None:
        """Local clock lags server by 30 s → offset +30 000 ms."""
        local_now_s = int(time.time())
        server_now_s = local_now_s + 30  # server is 30 s ahead

        with patch("time.time", return_value=float(local_now_s)):
            _make_client(
                sync_time=True,
                server_time_response=_server_time_response(server_now_s),
            )

        # The patched generator should return a value near server_now_s * 1000
        ts = _pybit_helpers.generate_timestamp()
        # Allow ±2 s for timing jitter in the test itself
        assert abs(ts - server_now_s * 1_000) < 2_000

    def test_large_negative_offset_patches_generator(self) -> None:
        """Local clock leads server by 40 s → offset −40 000 ms."""
        local_now_s = int(time.time())
        server_now_s = local_now_s - 40  # server is 40 s behind

        with patch("time.time", return_value=float(local_now_s)):
            _make_client(
                sync_time=True,
                server_time_response=_server_time_response(server_now_s),
            )

        ts = _pybit_helpers.generate_timestamp()
        assert abs(ts - server_now_s * 1_000) < 2_000

    def test_negligible_offset_does_not_patch_generator(self) -> None:
        """Offset < 500 ms → generator left untouched."""
        original = _pybit_helpers.generate_timestamp

        local_now_s = int(time.time())
        server_now_s = local_now_s  # clocks in sync

        with patch("time.time", return_value=float(local_now_s)):
            _make_client(
                sync_time=True,
                server_time_response=_server_time_response(server_now_s),
            )

        # Function object should still be the original (or at least a
        # different closure would give the same values — compare output).
        ts = _pybit_helpers.generate_timestamp()
        assert abs(ts - local_now_s * 1_000) < 1_000

    def test_offset_value_accuracy(self) -> None:
        """The applied offset must equal server_ms − local_midpoint_ms."""
        local_now_s = int(time.time())
        offset_s = 20
        server_now_s = local_now_s + offset_s

        frozen_local_ms = local_now_s * 1_000

        # Wir halten die Zeit für den gesamten Vorgang an:
        with patch("time.time", return_value=float(local_now_s)):
            _make_client(
                sync_time=True,
                server_time_response=_server_time_response(server_now_s),
            )

            # Dieser Aufruf muss INSIDE den with-block, damit er den Mock nutzt
            ts = _pybit_helpers.generate_timestamp()

        expected = frozen_local_ms + offset_s * 1_000
        # Jetzt sollte der Unterschied 0 sein, da die Zeit stillsteht
        assert abs(ts - expected) < 500


# ---------------------------------------------------------------------------
# TestTimeSyncFailures — graceful degradation
# ---------------------------------------------------------------------------


class TestTimeSyncFailures:
    def setup_method(self) -> None:
        self._original_gen = _pybit_helpers.generate_timestamp

    def teardown_method(self) -> None:
        _pybit_helpers.generate_timestamp = self._original_gen

    def _generator_is_unpatched(self) -> bool:
        """True if generate_timestamp still returns local time (no large offset)."""
        ts = _pybit_helpers.generate_timestamp()
        local_ms = int(time.time() * 1_000)
        return abs(ts - local_ms) < 1_000

    def test_network_error_does_not_raise(self) -> None:
        """A connection error during server-time fetch must not crash __init__."""
        _make_client(
            sync_time=True,
            server_time_raises=ConnectionError("timeout"),
        )  # should not raise

    def test_network_error_leaves_generator_unpatched(self) -> None:
        _make_client(
            sync_time=True,
            server_time_raises=ConnectionError("timeout"),
        )
        assert self._generator_is_unpatched()

    def test_non_zero_ret_code_does_not_raise(self) -> None:
        _make_client(
            sync_time=True,
            server_time_response=_server_time_response(0, ret_code=10001),
        )  # should not raise

    def test_non_zero_ret_code_leaves_generator_unpatched(self) -> None:
        _make_client(
            sync_time=True,
            server_time_response=_server_time_response(0, ret_code=10001),
        )
        assert self._generator_is_unpatched()

    def test_missing_result_key_does_not_raise(self) -> None:
        bad_response = {"retCode": 0, "retMsg": "OK"}  # no "result" key
        _make_client(sync_time=True, server_time_response=bad_response)

    def test_missing_result_key_leaves_generator_unpatched(self) -> None:
        bad_response = {"retCode": 0, "retMsg": "OK"}
        _make_client(sync_time=True, server_time_response=bad_response)
        assert self._generator_is_unpatched()

    def test_missing_time_second_key_does_not_raise(self) -> None:
        bad_response = {"retCode": 0, "result": {"timeNano": "12345"}}
        _make_client(sync_time=True, server_time_response=bad_response)

    def test_non_numeric_time_second_does_not_raise(self) -> None:
        bad_response = {"retCode": 0, "result": {"timeSecond": "NOT_A_NUMBER"}}
        _make_client(sync_time=True, server_time_response=bad_response)

    def test_non_numeric_time_second_leaves_generator_unpatched(self) -> None:
        bad_response = {"retCode": 0, "result": {"timeSecond": "NOT_A_NUMBER"}}
        _make_client(sync_time=True, server_time_response=bad_response)
        assert self._generator_is_unpatched()


# ---------------------------------------------------------------------------
# TestGetServerTimeMs
# ---------------------------------------------------------------------------


class TestGetServerTimeMs:
    def test_returns_milliseconds_from_time_second(self) -> None:
        client = _make_client(sync_time=False)
        client._mock_session.get_server_time.return_value = (  # type: ignore[attr-defined]
            _server_time_response(1_700_000_000)
        )
        assert client.get_server_time_ms() == 1_700_000_000_000

    def test_raises_on_non_zero_ret_code(self) -> None:
        from api.bybit_client import BybitAPIError

        client = _make_client(sync_time=False)
        client._mock_session.get_server_time.return_value = (  # type: ignore[attr-defined]
            _server_time_response(0, ret_code=10002)
        )
        with pytest.raises(BybitAPIError):
            client.get_server_time_ms()

    def test_raises_on_network_error(self) -> None:
        from api.bybit_client import BybitAPIError

        client = _make_client(sync_time=False)
        client._mock_session.get_server_time.side_effect = (  # type: ignore[attr-defined]
            ConnectionError("timed out")
        )
        with pytest.raises(BybitAPIError, match="Failed to fetch server time"):
            client.get_server_time_ms()

    def test_raises_on_malformed_response(self) -> None:
        from api.bybit_client import BybitAPIError

        client = _make_client(sync_time=False)
        client._mock_session.get_server_time.return_value = (  # type: ignore[attr-defined]
            {"retCode": 0, "result": {}}  # missing timeSecond
        )
        with pytest.raises(BybitAPIError, match="Unexpected server-time response"):
            client.get_server_time_ms()

    def test_error_message_contains_detail(self) -> None:
        from api.bybit_client import BybitAPIError

        client = _make_client(sync_time=False)
        client._mock_session.get_server_time.side_effect = (  # type: ignore[attr-defined]
            RuntimeError("SSL handshake failed")
        )
        with pytest.raises(BybitAPIError, match="SSL handshake failed"):
            client.get_server_time_ms()
