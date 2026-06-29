from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "MiMotion H5 Service"
    database_url: str = "sqlite:///./mimotion_service.db"
    jwt_secret: str = Field(default="change-me-in-production", min_length=16)
    token_aes_key: str = Field(default="1234567890abcdef", min_length=16, max_length=16)
    h5_base_url: str = "http://localhost:8000/app"
    allow_dev_login: bool = True
    wechat_appid: str | None = None
    wechat_secret: str | None = None
    wechat_web_appid: str | None = None
    wechat_web_secret: str | None = None
    wechat_oauth_redirect_uri: str | None = None
    wechat_oauth_scope: str = "snsapi_base"
    jwt_expire_minutes: int = 60 * 24 * 7
    h5_ticket_expire_seconds: int = 300
    oauth_state_expire_seconds: int = 300
    min_steps: int = 1
    max_steps: int = 98800

    @property
    def has_wechat_credentials(self) -> bool:
        return bool(self.wechat_appid and self.wechat_secret)

    @property
    def has_wechat_web_credentials(self) -> bool:
        return bool(self.wechat_web_appid and self.wechat_web_secret and self.wechat_oauth_redirect_uri)


@lru_cache
def get_settings() -> Settings:
    return Settings()
