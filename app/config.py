from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    email_host: str = "smtp.gmail.com"
    email_port: int = 587
    email_use_tls: bool = True
    email_host_user: str
    email_host_password: str

    imap_host: str = "imap.gmail.com"
    imap_port: int = 993

    test_email: str | None = None
    kindle_address: str
    max_attachment_mb: int = 18

    @property
    def max_attachment_bytes(self) -> int:
        return self.max_attachment_mb * 1024 * 1024


settings = Settings()