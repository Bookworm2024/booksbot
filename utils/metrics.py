"""
utils/metrics.py — lightweight in-process metrics.

Cheap counters incremented on the hot path (one dict write, no I/O) plus a
process start time for uptime. Reset on restart by design — for durable history
use the `errors` collection (utils.errors) or external monitoring. Surfaced in
the admin 🩺 Health view.
"""
import time

_counters: dict[str, int] = {
    "updates": 0, "messages": 0, "callbacks": 0, "errors": 0,
}
_start_ts: float = time.time()


def mark_start() -> None:
    global _start_ts
    _start_ts = time.time()


def incr(key: str, n: int = 1) -> None:
    _counters[key] = _counters.get(key, 0) + n


def uptime_seconds() -> int:
    return int(time.time() - _start_ts)


def uptime_str() -> str:
    s = uptime_seconds()
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def snapshot() -> dict:
    snap = dict(_counters)
    snap["uptime_seconds"] = uptime_seconds()
    snap["uptime"] = uptime_str()
    upd = snap.get("updates", 0)
    snap["error_rate"] = round(100.0 * snap.get("errors", 0) / upd, 2) if upd else 0.0
    return snap
