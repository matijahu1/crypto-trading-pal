"""
utils/time_utils.py — shared time/date conversion helpers.

These are pure functions with no external dependencies.
Import them wherever a timestamp needs to be formatted for output.
"""

from __future__ import annotations

import datetime


def ms_timestamp_to_date_time(ms: str | int) -> tuple[str, str]:
    """
    Convert a Unix millisecond timestamp to a (date, time) string pair.

    The timestamp is interpreted as UTC.

    Args:
        ms: Millisecond timestamp as a string (e.g. "1700000000000") or int.
            An empty string or 0 returns ("", "") rather than raising.

    Returns:
        Tuple of (date_str, time_str) where:
            date_str — "YYYY-MM-DD"  e.g. "2023-11-14"
            time_str — "HH:MM:SS"   e.g. "22:13:20"

    Examples:
        >>> ms_timestamp_to_date_time("1700000000000")
        ('2023-11-14', '22:13:20')

        >>> ms_timestamp_to_date_time("")
        ('', '')
    """
    if not ms or ms == "0" or ms == 0:
        return ("", "")

    dt = datetime.datetime.fromtimestamp(int(ms) / 1000, tz=datetime.timezone.utc)
    return (dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"))
