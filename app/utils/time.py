"""Timestamp serialization helper.

All datetime columns in this codebase are stored as naive UTC (via
`datetime.utcnow`). `datetime.isoformat()` on a naive value omits the
timezone, so `new Date(isoString)` on the frontend parses it as local time
instead of UTC — a task that just completed can render as "5 hours ago" for
a user in UTC+5. Appending "Z" makes the string unambiguously UTC.
"""
from datetime import datetime
from typing import Optional


def to_iso_z(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.isoformat()
    return dt.isoformat() + "Z"
