from dataclasses import dataclass, fields


@dataclass
class Settings:
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

    @property
    def max_attachment_bytes(self) -> int:
        return self.max_attachment_mb * 1024 * 1024

    def update(self, **values: object) -> None:
        unknown = values.keys() - {field.name for field in fields(self)}
        if unknown:
            raise KeyError(f"Unknown settings: {', '.join(sorted(unknown))}")
        for name, value in values.items():
            setattr(self, name, value)


settings = Settings()
