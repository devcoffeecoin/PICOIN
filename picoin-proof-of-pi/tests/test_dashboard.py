from pathlib import Path

from app.main import STATIC_DIR, WEB_DIR, app, dashboard, web_wallet


def test_dashboard_route_is_registered() -> None:
    paths = {getattr(route, "path", None) for route in app.routes}
    response = dashboard()

    assert "/dashboard" in paths
    assert "/dashboard/" in paths
    assert Path(response.path) == WEB_DIR / "dashboard.html"


def test_wallet_route_is_registered() -> None:
    paths = {getattr(route, "path", None) for route in app.routes}
    response = web_wallet()

    assert "/wallet" in paths
    assert "/wallet/" in paths
    assert Path(response.path) == WEB_DIR / "wallet.html"


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


def test_wallet_static_assets() -> None:
    html = (WEB_DIR / "wallet.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "wallet.css").read_text(encoding="utf-8")
    js = (STATIC_DIR / "wallet.js").read_text(encoding="utf-8")

    assert "Picoin Wallet" in html
    assert "https://api.picoin.science" in html
    assert "Create wallet" in html
    assert "Import JSON" in html
    assert "Sign and submit" in html
    assert "Last transaction hash" in html
    assert "/wallet/static/wallet.css" in html
    assert "/wallet/static/wallet.js" in html
    assert "PICOIN_UNIT" in js
    assert "amount_units" in js
    assert "fee_units" in js
    assert "canonicalAmount" in js
    assert "transactions/submit" in js
    assert "hero-copy" in css
