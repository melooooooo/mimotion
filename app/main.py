import base64
import json
import hmac
from datetime import timedelta
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, init_db
from app.models import AuthTicket, StepSubmission, User, ZeppBinding
from app.security import create_jwt, create_random_token, current_user, decrypt_json, encrypt_json, hash_token, now_utc
from app.zepp_service import ZeppAuthExpired, bind_account, submit_steps


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="MiMotion H5 Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MiniappLoginRequest(BaseModel):
    code: str = Field(min_length=1)


class H5ExchangeRequest(BaseModel):
    ticket: str = Field(min_length=1)


class ZeppBindRequest(BaseModel):
    account: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class StepSubmitRequest(BaseModel):
    steps: int


def get_or_create_user(db: Session, openid: str) -> User:
    user = db.execute(select(User).where(User.openid == openid)).scalar_one_or_none()
    if user is not None:
        return user
    user = User(openid=openid)
    db.add(user)
    db.flush()
    return user


def create_h5_ticket(db: Session, user: User, settings: Settings) -> str:
    ticket = create_random_token()
    db.add(
        AuthTicket(
            ticket_hash=hash_token(ticket),
            user_id=user.id,
            expires_at=now_utc() + timedelta(seconds=settings.h5_ticket_expire_seconds),
        )
    )
    return ticket


def h5_url_with_ticket(settings: Settings, ticket: str, **extra_params: str) -> str:
    params = {"ticket": ticket, **extra_params}
    return f"{settings.h5_base_url}?{urlencode(params)}"


def create_oauth_state(settings: Settings) -> str:
    payload = {
        "i": int(now_utc().timestamp()),
        "n": create_random_token()[:22],
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    signature = hash_token(f"{encoded}:{settings.jwt_secret}")[:24]
    return f"{encoded}.{signature}"


def verify_oauth_state(state: str, settings: Settings) -> None:
    try:
        encoded, signature = state.rsplit(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="微信登录状态无效") from exc
    expected = hash_token(f"{encoded}:{settings.jwt_secret}")[:24]
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail="微信登录状态无效")
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
        issued_at = int(payload["i"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail="微信登录状态无效") from exc
    if int(now_utc().timestamp()) - issued_at > settings.oauth_state_expire_seconds:
        raise HTTPException(status_code=400, detail="微信登录状态已过期")


async def resolve_openid(code: str, settings: Settings) -> str:
    if settings.has_wechat_credentials:
        params = {
            "appid": settings.wechat_appid,
            "secret": settings.wechat_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("https://api.weixin.qq.com/sns/jscode2session", params=params)
        data = resp.json()
        if "openid" not in data:
            raise HTTPException(status_code=400, detail=f"微信登录失败：{data.get('errmsg', data)}")
        return data["openid"]
    if settings.allow_dev_login:
        return "dev_" + hash_token(code)[:24]
    raise HTTPException(status_code=503, detail="未配置微信小程序登录能力")


async def resolve_web_openid(code: str, settings: Settings) -> str:
    if not settings.has_wechat_web_credentials:
        raise HTTPException(status_code=503, detail="未配置微信公众号网页授权能力")
    params = {
        "appid": settings.wechat_web_appid,
        "secret": settings.wechat_web_secret,
        "code": code,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get("https://api.weixin.qq.com/sns/oauth2/access_token", params=params)
    data = resp.json()
    if "openid" not in data:
        raise HTTPException(status_code=400, detail=f"微信网页授权失败：{data.get('errmsg', data)}")
    return data["openid"]


def binding_payload(binding: ZeppBinding | None) -> dict:
    return {
        "hasZeppBinding": binding is not None,
        "maskedAccount": binding.masked_account if binding else None,
    }


@app.post("/api/auth/miniapp-login")
async def miniapp_login(
    payload: MiniappLoginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    openid = await resolve_openid(payload.code, settings)
    user = get_or_create_user(db, openid)
    ticket = create_h5_ticket(db, user, settings)
    db.commit()
    h5_url = h5_url_with_ticket(settings, ticket)
    return {"h5Url": h5_url}


@app.get("/wechat-login")
def wechat_login(
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if not settings.has_wechat_web_credentials:
        raise HTTPException(status_code=503, detail="未配置微信公众号网页授权能力")
    state = create_oauth_state(settings)
    params = {
        "appid": settings.wechat_web_appid,
        "redirect_uri": settings.wechat_oauth_redirect_uri,
        "response_type": "code",
        "scope": settings.wechat_oauth_scope,
        "state": state,
    }
    return RedirectResponse(url=f"https://open.weixin.qq.com/connect/oauth2/authorize?{urlencode(params)}#wechat_redirect")


@app.get("/api/auth/wechat-oauth/callback")
async def wechat_oauth_callback(
    code: str = Query(min_length=1),
    state: str = Query(min_length=1),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    verify_oauth_state(state, settings)
    openid = await resolve_web_openid(code, settings)
    user = get_or_create_user(db, f"wechat_h5:{openid}")
    ticket = create_h5_ticket(db, user, settings)
    db.commit()
    return RedirectResponse(url=h5_url_with_ticket(settings, ticket, source="wechat_h5"))


@app.get("/dev-login")
async def dev_login(
    code: str = Query(default="local-dev-user"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.allow_dev_login:
        raise HTTPException(status_code=404, detail="开发登录未启用")
    user = get_or_create_user(db, "dev_" + hash_token(code)[:24])
    ticket = create_h5_ticket(db, user, settings)
    db.commit()
    return RedirectResponse(url=f"/app?{urlencode({'ticket': ticket, 'dev': '1'})}")


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/app/")


@app.get("/app")
def app_entry(ticket: str | None = None, dev: str | None = None) -> RedirectResponse:
    params = {}
    if ticket:
        params["ticket"] = ticket
    if dev:
        params["dev"] = dev
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"/app/{suffix}")


@app.post("/api/auth/h5-exchange")
def h5_exchange(
    payload: H5ExchangeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    ticket = db.execute(select(AuthTicket).where(AuthTicket.ticket_hash == hash_token(payload.ticket))).scalar_one_or_none()
    if ticket is None or ticket.consumed_at is not None or ticket.expires_at < now_utc():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录票据无效或已过期")
    ticket.consumed_at = now_utc()
    user = db.get(User, ticket.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    db.commit()
    return {"token": create_jwt(user.id, settings), **binding_payload(user.zepp_binding)}


@app.get("/api/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"userId": user.id, **binding_payload(user.zepp_binding)}


@app.post("/api/zepp/bind")
def zepp_bind(
    payload: ZeppBindRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        result = bind_account(payload.account, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    blob = encrypt_json(result.token_info, settings)
    binding = user.zepp_binding
    if binding is None:
        binding = ZeppBinding(user_id=user.id, masked_account=result.masked_account, token_blob=blob)
        db.add(binding)
    else:
        binding.masked_account = result.masked_account
        binding.token_blob = blob
    db.commit()
    return {"bound": True, "maskedAccount": result.masked_account}


@app.delete("/api/zepp/bind")
def zepp_unbind(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if user.zepp_binding is not None:
        db.delete(user.zepp_binding)
        db.commit()
    return {"bound": False}


@app.post("/api/steps/submit")
def steps_submit(
    payload: StepSubmitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    if payload.steps < settings.min_steps or payload.steps > settings.max_steps:
        raise HTTPException(status_code=400, detail=f"步数范围应为 {settings.min_steps}-{settings.max_steps}")
    binding = user.zepp_binding
    if binding is None:
        raise HTTPException(status_code=400, detail="请先绑定 Zepp Life 账号")
    token_info = decrypt_json(binding.token_blob, settings)
    try:
        success, message, token_info = submit_steps(token_info, payload.steps)
    except ZeppAuthExpired as exc:
        db.add(StepSubmission(user_id=user.id, steps=payload.steps, success=False, message=str(exc)))
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    binding.token_blob = encrypt_json(token_info, settings)
    record = StepSubmission(user_id=user.id, steps=payload.steps, success=success, message=message)
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"success": success, "steps": payload.steps, "message": message, "submittedAt": record.created_at.isoformat()}


@app.get("/api/steps/history")
def steps_history(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    records = db.execute(
        select(StepSubmission)
        .where(StepSubmission.user_id == user.id)
        .order_by(StepSubmission.created_at.desc(), StepSubmission.id.desc())
        .limit(20)
    ).scalars()
    return {
        "items": [
            {
                "steps": item.steps,
                "success": item.success,
                "message": item.message,
                "createdAt": item.created_at.isoformat(),
            }
            for item in records
        ]
    }


app.mount("/app", StaticFiles(directory="web", html=True), name="h5")
