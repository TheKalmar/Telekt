import re

from fastapi.testclient import TestClient

from digital_company import web


def test_frontend_loads_domain_modules_in_dependency_order():
    html = (web.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    sources = re.findall(r'<script src="([^"]+)"', html)

    assert sources == [
        "/assets/i18n.js",
        "/assets/ui-core.js",
        "/assets/settings-ui.js",
        "/assets/browser-ui.js",
    ]
    assert html.index("/assets/ui-core.js") < html.index("/assets/settings-ui.js")
    assert "function openPolicySettings" not in html
    assert "function openBrowserCockpit" not in html


def test_registered_frontend_assets_are_served_and_unknown_files_are_denied():
    client = TestClient(web.app)

    index = client.get("/")
    assert index.status_code == 200
    assert 'name="viewport"' in index.text
    assert 'class="mobileNav"' in index.text
    for name in sorted(web.FRONTEND_JS_ASSETS):
        response = client.get(f"/assets/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/javascript")
    assert client.get("/assets/secrets.env").status_code == 404


def test_dynamic_html_modules_use_the_shared_escape_boundary():
    core = (web.STATIC_DIR / "ui-core.js").read_text(encoding="utf-8")
    settings = (web.STATIC_DIR / "settings-ui.js").read_text(encoding="utf-8")

    for encoded in ["&amp;", "&lt;", "&gt;", "&quot;", "&#39;"]:
        assert encoded in core
    assert settings.count("innerHTML=") >= 4
    assert settings.count("esc(") >= 20
    assert "api_key:connectionApiKey.value.trim()" in settings
    assert "connectionApiKey.value=''" in settings
