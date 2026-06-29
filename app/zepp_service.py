import math
import uuid
from dataclasses import dataclass

import util.zepp_helper as zepp_helper


class ZeppAuthExpired(Exception):
    pass


@dataclass
class ZeppBindingResult:
    token_info: dict
    masked_account: str


def normalize_account(account: str) -> tuple[str, bool]:
    value = account.strip()
    if not value:
        raise ValueError("Zepp Life 账号不能为空")
    if value.startswith("+86") or "@" in value:
        normalized = value
    else:
        normalized = "+86" + value
    return normalized, normalized.startswith("+86")


def mask_account(account: str) -> str:
    if len(account) <= 8:
        size = max(math.floor(len(account) / 3), 1)
        return f"{account[:size]}***{account[-size:]}"
    return f"{account[:3]}****{account[-4:]}"


def bind_account(account: str, password: str) -> ZeppBindingResult:
    normalized, is_phone = normalize_account(account)
    if not password:
        raise ValueError("Zepp Life 密码不能为空")
    device_id = str(uuid.uuid4())
    access_token, msg = zepp_helper.login_access_token(normalized, password)
    if access_token is None:
        raise ValueError(f"Zepp 登录失败：{msg}")
    login_token, app_token, zepp_user_id, msg = zepp_helper.grant_login_tokens(access_token, device_id, is_phone)
    if login_token is None:
        raise ValueError(f"Zepp 授权失败：{msg}")
    token_info = {
        "access_token": access_token,
        "login_token": login_token,
        "app_token": app_token,
        "user_id": zepp_user_id,
        "device_id": device_id,
        "is_phone": is_phone,
    }
    return ZeppBindingResult(token_info=token_info, masked_account=mask_account(normalized))


def ensure_app_token(token_info: dict) -> tuple[str, dict]:
    app_token = token_info.get("app_token")
    zepp_user_id = token_info.get("user_id")
    if app_token:
        ok, _ = zepp_helper.check_app_token(app_token)
        if ok:
            return app_token, token_info
    login_token = token_info.get("login_token")
    if login_token:
        app_token, _ = zepp_helper.grant_app_token(login_token)
        if app_token:
            token_info["app_token"] = app_token
            return app_token, token_info
    access_token = token_info.get("access_token")
    device_id = token_info.get("device_id")
    if access_token and device_id:
        login_token, app_token, user_id, _ = zepp_helper.grant_login_tokens(
            access_token, device_id, bool(token_info.get("is_phone"))
        )
        if login_token and app_token:
            token_info["login_token"] = login_token
            token_info["app_token"] = app_token
            token_info["user_id"] = user_id or zepp_user_id
            return app_token, token_info
    raise ZeppAuthExpired("Zepp 登录状态已失效，请重新绑定账号")


def submit_steps(token_info: dict, steps: int) -> tuple[bool, str, dict]:
    app_token, token_info = ensure_app_token(token_info)
    ok, msg = zepp_helper.post_fake_brand_data(str(steps), app_token, token_info.get("user_id"))
    return ok, msg, token_info
