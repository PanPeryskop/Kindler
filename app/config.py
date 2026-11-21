from pydantic_settings import BaseSettings, SettingsConfigDict

from kindler.settings import settings


class EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    email_host: str = "smtp.gmail.com"
    email_port: int = 587
    email_use_tls: bool = True
    email_host_user: str = ""
    email_host_password: str = ""

    test_email: str | None = None
    max_attachment_mb: int = 18
    db_path: str = "kindler.db"
    search_limit: int = 10
    catalog_ttl_minutes: int = 360
    zlib_email: str | None = None
    zlib_password: str | None = None
    zlib_mirror: str = "https://z-lib.sk"
    zlib_cookies_file: str = "cookies.txt"
    zlib_state_file: str = "zlib_state.json"
    zlib_tor_proxy: str | None = None


settings.update(**EnvironmentSettings().model_dump())

__all__ = ["settings"]
