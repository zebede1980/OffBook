import os
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.1.0"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    secret_key: str
    secure_cookies: bool
    web_dir: Path
    nanogpt_api_key: str
    nanogpt_base_url: str
    parse_model: str
    kokoro_url: str
    zonos_url: str
    default_engine: str
    attempt_days: int
    start_worker: bool

    @property
    def db_path(self) -> Path:
        return self.data_dir / "offbook.db"


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def load_dotenv(path: Path = Path(".env")) -> None:
    """Local runs only: fill os.environ from ./.env without overriding real environment variables.
    (Docker passes the same file via env_file.)"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("OFFBOOK_DATA_DIR", "data")).resolve(),
        secret_key=os.environ.get("OFFBOOK_SECRET_KEY", "dev-only-change-me"),
        secure_cookies=_flag("OFFBOOK_SECURE_COOKIES"),
        web_dir=Path(os.environ.get("OFFBOOK_WEB_DIR", Path(__file__).resolve().parents[2] / "web" / "build")),
        nanogpt_api_key=os.environ.get("NANOGPT_API_KEY", ""),
        nanogpt_base_url=os.environ.get("NANOGPT_BASE_URL", "https://nano-gpt.com/api/v1"),
        parse_model=os.environ.get("PARSE_MODEL", "anthropic/claude-haiku-5.5"),
        kokoro_url=os.environ.get("KOKORO_URL", "http://localhost:8880"),
        zonos_url=os.environ.get("ZONOS_URL", ""),
        default_engine=os.environ.get("DEFAULT_TTS_ENGINE", "kokoro"),
        attempt_days=int(os.environ.get("ATTEMPT_RETENTION_DAYS", "7")),
        start_worker=_flag("OFFBOOK_WORKER", "1"),
    )
