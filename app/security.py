import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import User
from util.aes_help import decrypt_data, encrypt_data

bearer_scheme = HTTPBearer(auto_error=False)


def now_utc() -> datetime:
    return datetime.utcnow()


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_random_token() -> str:
    return secrets.token_urlsafe(32)


def create_jwt(user_id: int, settings: Settings) -> str:
    expires_at = now_utc() + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "exp": expires_at, "iat": now_utc()}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def encrypt_json(data: dict[str, Any], settings: Settings) -> bytes:
    return encrypt_data(json.dumps(data, ensure_ascii=False).encode("utf-8"), settings.token_aes_key.encode("utf-8"))


def decrypt_json(blob: bytes, settings: Settings) -> dict[str, Any]:
    plain = decrypt_data(blob, settings.token_aes_key.encode("utf-8"))
    return json.loads(plain.decode("utf-8"))


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user
