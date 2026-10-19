"""Read-only queries for the dashboard. Open-close per call; never hold a connection."""
import sqlite3
import time

from database.database import db_path  # single source of truth for the file location


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)  # read-only intent
    con.row_factory = sqlite3.Row
    return con


def _safe(fn, fallback):
    """Run fn(); on missing/locked DB return fallback instead of raising."""
    try:
        return fn()
    except sqlite3.Error:
        return fallback


def latest_events(limit: int = 100) -> list[dict]:
    def _q():
        with _connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT event_id, node_id, predicted_drone, model_confidence, timestamp "
                "FROM detection_events ORDER BY event_id DESC LIMIT ?", (limit,))]
    return _safe(_q, [])


def event_count() -> int:
    def _q():
        with _connect() as con:
            row = con.execute("SELECT COUNT(*) FROM detection_events").fetchone()
            return int(row[0])
    return _safe(_q, 0)


def drone_count() -> int:
    """Total predicted-drone rows (no wall-clock filter: timestamps are ESP32 boot-ns)."""
    def _q():
        with _connect() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM detection_events WHERE predicted_drone = 1").fetchone()
            return int(row[0])
    return _safe(_q, 0)


def confidence_series(limit: int = 300) -> list[tuple[int, float]]:
    """(event_id, confidence) oldest-first for the SVG chart."""
    def _q():
        with _connect() as con:
            rows = con.execute(
                "SELECT event_id, model_confidence FROM detection_events "
                "ORDER BY event_id DESC LIMIT ?", (limit,)).fetchall()
            return [(r[0], float(r[1])) for r in reversed(rows)]
    return _safe(_q, [])


def distinct_nodes() -> list[int]:
    def _q():
        with _connect() as con:
            return [r[0] for r in con.execute(
                "SELECT DISTINCT node_id FROM detection_events ORDER BY node_id").fetchall()]
    return _safe(_q, [])


def max_event_id() -> int | None:
    """Change-detection anchor: skip re-render when unchanged."""
    def _q():
        with _connect() as con:
            row = con.execute("SELECT MAX(event_id) FROM detection_events").fetchone()
            return int(row[0]) if row and row[0] is not None else None
    return _safe(_q, None)


def db_write_time() -> float | None:
    """Host mtime of events.db = last pipeline write (proxy for node liveness)."""
    try:
        return db_path.stat().st_mtime
    except OSError:
        return None


def seconds_since_write(now: float | None = None) -> float | None:
    mtime = db_write_time()
    if mtime is None:
        return None
    return max(0.0, (now if now is not None else time.time()) - mtime)
