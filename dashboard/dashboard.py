"""KaawayAn Console — Streamlit dashboard synced to database/events.db.

Run from repo root:  streamlit run dashboard/dashboard.py
Read-only: never writes to events.db. Live region refreshes via
st.fragment(run_every=1); static templates are cached per process.
"""
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import sys as _sys
from pathlib import Path as _P
# `streamlit run dashboard/dashboard.py` executes this file as __main__ with its
# own dir on sys.path, which makes `dashboard` resolve to THIS FILE (not a
# package). Fix: import siblings by path, then fall back to package imports.
_ROOT = str(_P(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
if str(_P(__file__).resolve().parent) not in _sys.path:
    _sys.path.insert(0, str(_P(__file__).resolve().parent))

try:
    from dashboard.db import (
        confidence_series,
        distinct_nodes,
        drone_count,
        event_count,
        latest_events,
        max_event_id,
        seconds_since_write,
    )
except ImportError:  # running as script: dashboard/ itself is on sys.path
    from db import (
        confidence_series,
        distinct_nodes,
        drone_count,
        event_count,
        latest_events,
        max_event_id,
        seconds_since_write,
    )

ASSETS = Path(__file__).parent / "ui_assets"
REFRESH_S = 1
ONLINE_STALE_S = 30  # no DB write within this long -> node considered stale


@st.cache_resource
def _load_template(name: str) -> str:
    import re
    tpl = (ASSETS / name / "code.html").read_text(encoding="utf-8")
    # Strip the template's own sidebar + top header: Streamlit provides the
    # app chrome (st.sidebar + page). Keeping both doubles navigation and
    # wastes ~400px. Remove <aside>...</aside> and the fixed <header>...</header>.
    tpl = re.sub(r"<aside.*?</aside>", "", tpl, flags=re.S)
    tpl = re.sub(r'<header class="fixed top-0.*?</header>', "", tpl, flags=re.S)
    # Unwrap the left-padding container the sidebar required.
    tpl = tpl.replace('<div class="pl-72">', "<div>").replace('<div class="pl-56">', "<div>")
    return tpl


def _wall_time() -> str:
    return datetime.now().strftime("%d %b %Y · %H:%M PHT")


def _live_badge_html() -> str:
    return (
        '<span class="px-space-sm py-[2px] rounded-full '
        'bg-surface-container text-telemetry-active '
        'font-data-mono-sm text-data-mono-sm tracking-wider uppercase">LIVE</span>'
    )


def _event_rows_html(rows: list[dict], limit: int = 8) -> str:
    """Render detection_events rows using the template's own row classes."""
    out = []
    for i, r in enumerate(rows[:limit]):
        eid = f"DET-{r['event_id']:03d}"
        conf = float(r["model_confidence"]) * 100
        drone = bool(r["predicted_drone"])
        if i == 0:
            cls = ("event-row bg-surface-container text-primary font-semibold "
                   "cursor-pointer transition-colors hover:bg-surface-container-high")
            dot = '<span class="w-2 h-2 rounded-full bg-telemetry-active"></span>'
            id_cls, conf_cls = "font-semibold", "text-primary font-bold"
        else:
            cls = ("event-row bg-surface-container-lowest text-on-surface cursor-pointer "
                   "transition-colors hover:bg-surface-container-low")
            dot = '<span class="w-1.5 h-1.5 rounded-full bg-outline-variant"></span>'
            id_cls, conf_cls = "text-on-surface-variant font-medium", "text-on-surface"
        label = "DRONE" if drone else "no-drone"
        out.append(
            f'<tr class="{cls}" data-conf="{conf:.1f}%" data-event-id="{eid}" '
            f'data-node="{r["node_id"]}">'
            f'<td class="py-3.5 px-space-lg flex items-center gap-space-xs {id_cls}">{dot}<span>{eid}</span></td>'
            f'<td class="py-3.5 px-space-md text-on-surface">node {r["node_id"]}</td>'
            f'<td class="py-3.5 px-space-md text-right {conf_cls}">{conf:.1f}%</td>'
            f'<td class="py-3.5 px-space-md text-right text-secondary">{label}</td>'
            f'<td class="py-3.5 px-space-lg text-right text-secondary">#{r["event_id"]}</td>'
            f"</tr>"
        )
    if not out:
        out.append(
            '<tr><td class="py-3.5 px-space-lg text-secondary" colspan="5">'
            "No events yet — start the pipeline (python main.py).</td></tr>"
        )
    return "\n".join(out)


def _curve_path(series: list[tuple[float, float]], w: float = 920.0,
                x0: float = 60.0, y_top: float = 20.0, y_bot: float = 240.0) -> str:
    """Map normalized confidences to the template's SVG coordinate frame."""
    if not series:
        return ""
    n = len(series)
    pts = [(x0 + (w * i / max(n - 1, 1)), y_bot - float(c) * (y_bot - y_top))
           for i, (_, c) in enumerate(series)]
    d = f"M {pts[0][0]:.0f},{pts[0][1]:.0f} " + " ".join(
        f"L {x:.0f},{y:.0f}" for x, y in pts[1:])
    return d


def _render_detection_page(rows: list[dict]) -> None:
    import re
    tpl = _load_template("detection_history")
    tpl = tpl.replace("DEMO DATA", "LIVE")
    tpl = tpl.replace("19 Oct 2026 \u00b7 14:32 PHT", _wall_time())
    tpl = tpl.replace("19 Oct 2026 · 14:32 PHT", _wall_time())
    series = confidence_series(120)
    if rows:
        latest = rows[0]
        conf_pct = float(latest["model_confidence"]) * 100
        tpl = tpl.replace(">DET-024<", f">DET-{latest['event_id']:03d}<")
        tpl = tpl.replace(">94.2%<", f">{conf_pct:.1f}%<")
        tpl = tpl.replace('data-conf="94.2%"', f'data-conf="{conf_pct:.1f}%"')
        tpl = tpl.replace('data-event-id="DET-024"', f"data-event-id=\"DET-{latest['event_id']:03d}\"")
    curve = _curve_path([(i, c) for i, c in series])
    if curve:
        tpl = re.sub(r'<path d="M 60,200.*?id="event-curve"',
                     f'<path d="{curve}" id="event-curve"', tpl, flags=re.S)
    # Inject live rows: replace the demo tbody content between markers.
    live_rows = _event_rows_html(rows)
    tpl = re.sub(r'(<tbody[^>]*id="detection-table-body"[^>]*>).*?(</tbody>)',
                 lambda m: m.group(1) + live_rows + m.group(2), tpl, flags=re.S)
    tpl += "<!-- LIVE: event_rows, event_curve, wall_time -->"
    st.session_state[f"render_{('Detections', st.session_state.get('anchor'))}"] = tpl
    components.html(tpl, height=1100, scrolling=True)


def _render_overview(rows: list[dict], total: int, drones: int) -> None:
    tpl = _load_template("monitoring_overview")
    tpl = tpl.replace("DEMO DATA", "LIVE")
    tpl = tpl.replace("19 Oct 2026 \u00b7 14:32 PHT", _wall_time())
    tpl = tpl.replace("19 Oct 2026 · 14:32 PHT", _wall_time())
    stale = seconds_since_write()
    sync_txt = "SYNC" if (stale is not None and stale < ONLINE_STALE_S) else "STALE"
    tpl = tpl.replace("2/2 SYNC", f"{len(distinct_nodes())} node(s) {sync_txt}")
    tpl += f"<!-- LIVE: total={total} drones={drones} stale_s={stale} rows={len(rows)} -->"
    st.session_state[f"render_{('Overview', st.session_state.get('anchor'))}"] = tpl
    components.html(tpl, height=1100, scrolling=True)


def _render_environment() -> None:
    tpl = _load_template("environmental_conditions")
    notice = (
        '<div style="margin:16px;padding:16px;border:1px solid #E2E6DF;border-radius:12px;'
        'background:#EEF6ED;font-family:Inter">No environmental data yet — '
        "schema has no BME columns (see dashboard_plan.md Phase 4).</div>"
    )
    tpl = tpl.replace("<main", notice + "<main", 1)
    st.session_state[f"render_{('Environment', st.session_state.get('anchor'))}"] = tpl
    components.html(tpl, height=1100, scrolling=True)


def _render_about() -> None:
    tpl = _load_template("about_team")
    st.session_state[f"render_{('About', st.session_state.get('anchor'))}"] = tpl
    components.html(tpl, height=1100, scrolling=True)


st.set_page_config(page_title="KaawayAn Console", layout="wide")


@st.fragment(run_every=REFRESH_S)
def _live_fragment() -> None:
    """Re-runs every REFRESH_S seconds; keeps last render when DB is unchanged."""
    anchor = max_event_id()
    page = st.session_state.get("page", "Detections")
    if anchor is not None and st.session_state.get("anchor") == anchor:
        # Unchanged: re-render from cache (cheap, no DOM wipe) + status line.
        # Never `return` without rendering — that blanks the iframe.
        key = (page, anchor)
        html = st.session_state.get(f"render_{key}")
        if html is not None:
            components.html(html, height=1100, scrolling=True)
        st.caption(f"No new events (#{anchor}) — waiting for pipeline…")
        return
    st.session_state["anchor"] = anchor
    rows = latest_events(50)
    if page == "Detections":
        _render_detection_page(rows)
    elif page == "Overview":
        _render_overview(rows, event_count(), drone_count())
    elif page == "Environment":
        _render_environment()
    else:
        _render_about()


def main() -> None:
    st.sidebar.title("KaawayAn Console")
    st.sidebar.caption("Read-only · database/events.db")
    page = st.sidebar.radio("Page", ["Overview", "Detections", "Environment", "About"],
                            key="page")
    total, drones = event_count(), drone_count()
    st.sidebar.metric("Total events", total)
    st.sidebar.metric("Drone predictions", drones)
    stale = seconds_since_write()
    if stale is None:
        st.sidebar.warning("events.db not found yet")
    elif stale < ONLINE_STALE_S:
        st.sidebar.success(f"Pipeline live ({stale:.0f}s ago)")
    else:
        st.sidebar.warning(f"Pipeline stale ({stale:.0f}s ago)")
    _live_fragment()


if __name__ == "__main__":
    main()
