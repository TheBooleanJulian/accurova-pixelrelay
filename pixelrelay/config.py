"""Settings, read once from the environment (and an optional .env file).

Only the stdlib is used: a tiny .env reader instead of python-dotenv.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    app_env: str = "development"
    base_url: str = "http://localhost:8000"
    data_dir: Path = Path("data")
    secret_key: str = ""
    auth_mode: str = "dev"  # dev | supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    owner_emails: frozenset[str] = field(default_factory=frozenset)
    database_url: str = ""  # postgres://… (production); empty = SQLite in DATA_DIR
    db_schema: str = "accurova_pixelrelay"
    storage_provider: str = "local"  # local | r2
    r2_endpoint: str = ""  # https://<account id>.r2.cloudflarestorage.com
    r2_bucket: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    worker_mode: str = "thread"  # thread | off
    max_upload_mb: int = 80
    max_image_megapixels: int = 120
    trust_proxy: bool = False
    cookie_secure: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "pixelrelay.sqlite3"

    @property
    def db_target(self) -> Path | str:
        """What db.connect() takes: the Postgres URL when set, else the SQLite file."""
        return self.database_url or self.db_path

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def validate(self) -> None:
        if self.auth_mode not in {"dev", "supabase"}:
            raise RuntimeError("AUTH_MODE must be 'dev' or 'supabase'.")
        if self.worker_mode not in {"thread", "off"}:
            raise RuntimeError("WORKER_MODE must be 'thread' or 'off'.")
        if self.storage_provider not in {"local", "r2"}:
            raise RuntimeError("STORAGE_PROVIDER must be 'local' or 'r2'.")
        if self.storage_provider == "r2":
            missing = [
                name
                for name, value in (
                    ("R2_ENDPOINT", self.r2_endpoint),
                    ("R2_BUCKET", self.r2_bucket),
                    ("R2_ACCESS_KEY_ID", self.r2_access_key_id),
                    ("R2_SECRET_ACCESS_KEY", self.r2_secret_access_key),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(f"STORAGE_PROVIDER=r2 needs {', '.join(missing)}.")
            if not self.r2_endpoint.startswith(("https://", "http://127.0.0.1:")):  # plain http only for tests
                raise RuntimeError("R2_ENDPOINT must be an https:// URL.")
        if self.database_url and not self.database_url.startswith(("postgres://", "postgresql://")):
            raise RuntimeError("DATABASE_URL must be a postgres:// URL (leave it empty for SQLite).")
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", self.db_schema):
            raise RuntimeError("DB_SCHEMA must be a lower-case Postgres identifier.")
        if self.is_production:
            if self.auth_mode == "dev":
                raise RuntimeError("AUTH_MODE=dev is refused when APP_ENV=production.")
            if len(self.secret_key) < 32:
                raise RuntimeError("SECRET_KEY must be set (32+ characters) in production.")
            if not self.base_url.startswith("https://"):
                raise RuntimeError("BASE_URL must be https:// in production.")
        if self.auth_mode == "supabase" and not (self.supabase_url and self.supabase_anon_key):
            raise RuntimeError("AUTH_MODE=supabase needs SUPABASE_URL and SUPABASE_ANON_KEY.")


def load_settings(env: dict[str, str] | None = None, env_file: Path | None = None) -> Settings:
    """Build settings from (in order of precedence) `env`, the process env, then `.env`."""
    merged: dict[str, str] = {}
    merged.update(_read_dotenv(env_file if env_file is not None else Path(".env")))
    merged.update({k: v for k, v in os.environ.items()})
    if env:
        merged.update(env)

    base_url = merged.get("BASE_URL", "http://localhost:8000").rstrip("/")
    s = Settings(
        app_env=merged.get("APP_ENV", "development"),
        base_url=base_url,
        data_dir=Path(merged.get("DATA_DIR", "data")),
        secret_key=merged.get("SECRET_KEY", ""),
        auth_mode=merged.get("AUTH_MODE", "dev"),
        supabase_url=merged.get("SUPABASE_URL", "").rstrip("/"),
        supabase_anon_key=merged.get("SUPABASE_ANON_KEY", ""),
        owner_emails=frozenset(e.strip().lower() for e in merged.get("OWNER_EMAILS", "").split(",") if e.strip()),
        database_url=merged.get("DATABASE_URL", "").strip(),
        db_schema=merged.get("DB_SCHEMA", "accurova_pixelrelay"),
        storage_provider=merged.get("STORAGE_PROVIDER", "local"),
        r2_endpoint=merged.get("R2_ENDPOINT", "").rstrip("/"),
        r2_bucket=merged.get("R2_BUCKET", ""),
        r2_access_key_id=merged.get("R2_ACCESS_KEY_ID", ""),
        r2_secret_access_key=merged.get("R2_SECRET_ACCESS_KEY", ""),
        worker_mode=merged.get("WORKER_MODE", "thread"),
        max_upload_mb=int(merged.get("MAX_UPLOAD_MB", "80")),
        max_image_megapixels=int(merged.get("MAX_IMAGE_MEGAPIXELS", "120")),
        trust_proxy=_bool(merged.get("TRUST_PROXY"), False),
        cookie_secure=_bool(merged.get("COOKIE_SECURE"), base_url.startswith("https://")),
    )
    s.validate()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    if not s.secret_key:
        # Development only (validate() refuses this in production): keep a local
        # random secret so PINs stay stable across restarts.
        secret_file = s.data_dir / ".dev-secret"
        if secret_file.is_file():
            s.secret_key = secret_file.read_text(encoding="utf-8").strip()
        else:
            s.secret_key = secrets.token_urlsafe(48)
            secret_file.write_text(s.secret_key, encoding="utf-8")
    return s
