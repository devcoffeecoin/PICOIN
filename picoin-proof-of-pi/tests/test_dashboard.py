from pathlib import Path

from app.main import STATIC_DIR, WEB_DIR, app, dashboard


def test_dashboard_route_is_registered() -> None:
    paths = {getattr(route, "path", None) for route in app.routes}
    response = dashboard()

    assert "/dashboard" in paths
    assert "/dashboard/" in paths
    assert Path(response.path) == WEB_DIR / "dashboard.html"


def test_dashboard_static_assets() -> None:
    html = (WEB_DIR / "dashboard.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "dashboard.css").read_text(encoding="utf-8")
    js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")

    assert "Dashboard Local" in html
    assert "connectionBadge" in html
    assert "dashboardErrors" in html
    assert "explorer-panel" in html
    assert "validatorDisplayCount" in html
    assert "validatorSummary" in html
    assert "dashboardPerformance" in html
    assert 'value="3.1416"' in html
    assert "/dashboard/static/dashboard.css" in html
    assert "/dashboard/static/dashboard.js" in html
    assert "metric-grid" in css
    assert "connection-errors" in css
    assert "performance-layout" in css
    assert "fetchJson" in js
    assert "safeFetchJson" in js
    assert "renderConnection" in js
    assert "validatorDisplayCount" in js
    assert "slice(0, 100)" in js
    assert "dashboardPerformance" in js
    assert 'health: "/health"' in js
    assert 'node: "/node/status"' in js
    assert 'events: "/events?limit=14"' in js
    assert 'retroAudits: "/audit/retroactive?limit=8"' in js
    assert "state_root" in js
    assert 'fetchJson("/audit/retroactive/run?sample_multiplier=2"' in js
