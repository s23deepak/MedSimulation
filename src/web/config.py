import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "local"))
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            x.strip()
            for x in os.getenv(
                "ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
            ).split(",")
            if x.strip()
        ]
    )
    max_upload_bytes: int = 20 * 1024 * 1024

    def validate(self):
        if self.environment not in {"local", "demo", "production"}:
            raise ValueError("APP_ENV must be local, demo, or production")
        if "*" in self.allowed_origins:
            raise ValueError("ALLOWED_ORIGINS must contain explicit origins")
        if self.environment != "local":
            if not os.getenv("DATABASE_URL", "").startswith(("postgres",)):
                raise ValueError("Hosted deployments require a Postgres DATABASE_URL")
            if any(not origin.startswith("https://") for origin in self.allowed_origins):
                raise ValueError("Hosted origins must use HTTPS")
