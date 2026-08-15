import re

from fastapi.testclient import TestClient

from digital_company import web


def test_frontend_loads_domain_modules_in_dependency_order():
    html = (web.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    sources = re.findall(r'<script src="([^"]+)"', html)

    assert sources == [
        "/assets/i18n.js?v=9",
        "/assets/ui-core.js?v=9",
        "/assets/agent-ui.js?v=9",
        "/assets/settings-ui.js?v=9",
        "/assets/browser-ui.js?v=9",
    ]
    assert html.index("/assets/ui-core.js?v=9") < html.index("/assets/settings-ui.js?v=9")
    assert html.index("/assets/ui-core.js?v=9") < html.index("/assets/agent-ui.js?v=9")
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
        if name == "i18n.js":
            assert response.headers["cache-control"] == "no-store, max-age=0"
    assert client.get("/assets/secrets.env").status_code == 404


def test_dynamic_html_modules_use_the_shared_escape_boundary():
    core = (web.STATIC_DIR / "ui-core.js").read_text(encoding="utf-8")
    settings = (web.STATIC_DIR / "settings-ui.js").read_text(encoding="utf-8")
    agents = (web.STATIC_DIR / "agent-ui.js").read_text(encoding="utf-8")

    for encoded in ["&amp;", "&lt;", "&gt;", "&quot;", "&#39;"]:
        assert encoded in core
    assert settings.count("innerHTML=") >= 4
    assert settings.count("esc(") >= 20
    assert agents.count("esc(") >= 20
    assert "api_key:connectionApiKey.value.trim()" in settings
    assert "connectionApiKey.value=''" in settings


def test_company_creation_and_agent_configuration_are_separate():
    html = (web.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    agents = (web.STATIC_DIR / "agent-ui.js").read_text(encoding="utf-8")

    assert 'name="industry"' in html
    assert 'name="jurisdiction"' in html
    assert 'name="website_url"' in html
    assert 'name="autonomy_level"' not in html
    assert 'name="model_mode"' not in html
    assert "filter(x=>x.enabled)" in agents
    assert "renderAgentOverview" in agents


def test_email_settings_expose_smtp_connection_and_explicit_test_action():
    html = (web.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    settings = (web.STATIC_DIR / "settings-ui.js").read_text(encoding="utf-8")

    assert 'id="emailSmtpConnection"' in html
    assert 'id="emailFromAddress"' in html
    assert 'id="emailPublicBaseUrl"' in html
    assert "smtp_connection_id:emailSmtpConnection.value||null" in settings
    assert "'/api/settings/email/test'" in settings
    assert "createSmtpConnection" in settings
