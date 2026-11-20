"""
Shared atomic JSON persistence and date-based retention helpers.
"""

import os
import re
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

VN_TZ = timezone(timedelta(hours=7))
_DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def atomic_write_json(file_path: str, data: Any, indent: int = 2) -> None:
    """Writes JSON atomically via a temp file + os.replace() to avoid corruption."""
    parent = os.path.dirname(os.path.abspath(file_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{file_path}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp_path, file_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise


def prune_old_date_keys(data: Dict[str, Any], keep_days: int = 14) -> Dict[str, Any]:
    """Removes YYYY-MM-DD top-level keys older than keep_days (Vietnam TZ)."""
    if not isinstance(data, dict) or keep_days <= 0:
        return data
    cutoff = (datetime.now(VN_TZ).date() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    stale = [k for k in data if isinstance(k, str) and _DATE_KEY_RE.match(k) and k < cutoff]
    for k in stale:
        data.pop(k, None)
    return data
