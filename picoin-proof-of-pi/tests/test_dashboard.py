from pathlib import Path

from app.main import STATIC_DIR, WEB_DIR, app, dashboard


def test_dashboard_route_is_registered() -> None:
    paths = {getattr(route, "path", None) for route in app.routes}
    response = dashboard()

    assert "/dashboard" in paths
    assert Path(response.path) == WEB_DIR / "dashboard.html"


def test_dashboard_static_assets() -> None:
    html = (WEB_DIR / "dashboard.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "dashboard.css").read_text(encoding="utf-8")
    js = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")

    assert "Dashboard Local" in html
    assert "/dashboard/static/dashboard.css" in html
    assert "/dashboard/static/dashboard.js" in html
    assert "metric-grid" in css
    assert "fetchJson" in js
