import os
from urllib.parse import parse_qs, urlparse

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-secret-test-secret"
os.environ["TOKEN_AES_KEY"] = "1234567890abcdef"
os.environ["H5_BASE_URL"] = "http://testserver/app"
os.environ["ALLOW_DEV_LOGIN"] = "true"
os.environ["WECHAT_WEB_APPID"] = "web-appid"
os.environ["WECHAT_WEB_SECRET"] = "web-secret"
os.environ["WECHAT_OAUTH_REDIRECT_URI"] = "http://testserver/api/auth/wechat-oauth/callback"

from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def client():
    return TestClient(app)


def exchange_token(c: TestClient, code: str = "user-a") -> str:
    login = c.post("/api/auth/miniapp-login", json={"code": code})
    assert login.status_code == 200
    ticket = login.json()["h5Url"].split("ticket=")[1]
    exchange = c.post("/api/auth/h5-exchange", json={"ticket": ticket})
    assert exchange.status_code == 200
    return exchange.json()["token"]


def test_ticket_can_only_be_consumed_once():
    c = client()
    login = c.post("/api/auth/miniapp-login", json={"code": "one-shot"})
    ticket = login.json()["h5Url"].split("ticket=")[1]

    assert c.post("/api/auth/h5-exchange", json={"ticket": ticket}).status_code == 200
    assert c.post("/api/auth/h5-exchange", json={"ticket": ticket}).status_code == 401


def test_auth_required_for_me():
    c = client()
    assert c.get("/api/me").status_code == 401
    token = exchange_token(c)
    resp = c.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["hasZeppBinding"] is False


def test_submit_requires_binding():
    c = client()
    token = exchange_token(c)
    resp = c.post("/api/steps/submit", json={"steps": 18888}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400
    assert "请先绑定" in resp.json()["detail"]


def test_step_range_validation():
    c = client()
    token = exchange_token(c)
    resp = c.post("/api/steps/submit", json={"steps": 100000}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400


def test_wechat_login_redirects_to_oauth():
    c = client()
    resp = c.get("/wechat-login", follow_redirects=False)

    assert resp.status_code == 307
    location = resp.headers["location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.netloc == "open.weixin.qq.com"
    assert parsed.path == "/connect/oauth2/authorize"
    assert params["appid"] == ["web-appid"]
    assert params["redirect_uri"] == ["http://testserver/api/auth/wechat-oauth/callback"]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["snsapi_base"]
    assert params["state"][0]
    assert location.endswith("#wechat_redirect")


def test_wechat_oauth_callback_creates_h5_ticket(monkeypatch):
    async def fake_resolve_web_openid(code, settings):
        assert code == "wechat-code"
        return "openid-123"

    monkeypatch.setattr("app.main.resolve_web_openid", fake_resolve_web_openid)
    c = client()
    login = c.get("/wechat-login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    callback = c.get(f"/api/auth/wechat-oauth/callback?code=wechat-code&state={state}", follow_redirects=False)

    assert callback.status_code == 307
    location = callback.headers["location"]
    assert location.startswith("http://testserver/app?")
    params = parse_qs(urlparse(location).query)
    assert params["source"] == ["wechat_h5"]
    exchange = c.post("/api/auth/h5-exchange", json={"ticket": params["ticket"][0]})
    assert exchange.status_code == 200
    assert exchange.json()["hasZeppBinding"] is False


def test_wechat_oauth_callback_rejects_tampered_state(monkeypatch):
    async def fake_resolve_web_openid(code, settings):
        return "openid-123"

    monkeypatch.setattr("app.main.resolve_web_openid", fake_resolve_web_openid)
    c = client()

    resp = c.get("/api/auth/wechat-oauth/callback?code=wechat-code&state=tampered", follow_redirects=False)

    assert resp.status_code == 400


def test_dev_login_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_LOGIN", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        c = client()
        resp = c.get("/dev-login", follow_redirects=False)
        assert resp.status_code == 404
    finally:
        monkeypatch.setenv("ALLOW_DEV_LOGIN", "true")
        get_settings.cache_clear()
