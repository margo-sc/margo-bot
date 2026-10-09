"""Настройки из переменных окружения (файл .env на сервере)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _ids(value: str) -> list[int]:
    return [int(x) for x in value.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()]


@dataclass
class Config:
    bot_token: str
    admin_ids: list[int]
    alert_chat_id: int | None
    public_url: str
    web_host: str
    web_port: int
    data_dir: Path
    content_path: Path
    rk_login: str
    rk_pass1: str
    rk_pass2: str
    rk_test: bool
    rk_hash: str
    rk_receipt: bool
    rk_sno: str
    order_start: int
    claim_code: str = ""
    tick_seconds: int = 20
    extra: dict = field(default_factory=dict)

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.sqlite3"


def load_config(env_file: Path | None = None) -> Config:
    _load_dotenv(env_file or BASE_DIR / ".env")
    admins = _ids(os.environ.get("ADMIN_IDS", ""))
    data_dir = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
    claimed = data_dir / "admins.txt"  # владельцы, добавленные через /claim
    if claimed.exists():
        admins += [i for i in _ids(claimed.read_text().replace("\n", ",")) if i not in admins]
    alert = os.environ.get("ALERT_CHAT_ID", "").strip()
    return Config(
        bot_token=os.environ.get("BOT_TOKEN", ""),
        admin_ids=admins,
        alert_chat_id=int(alert) if alert.lstrip("-").isdigit() else (admins[0] if admins else None),
        public_url=os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/"),
        web_host=os.environ.get("WEB_HOST", "127.0.0.1"),
        web_port=int(os.environ.get("WEB_PORT", "8080")),
        data_dir=data_dir,
        content_path=Path(os.environ.get("CONTENT_PATH", str(BASE_DIR / "content.yaml"))),
        rk_login=os.environ.get("ROBOKASSA_LOGIN", ""),
        rk_pass1=os.environ.get("ROBOKASSA_PASS1", ""),
        rk_pass2=os.environ.get("ROBOKASSA_PASS2", ""),
        rk_test=os.environ.get("ROBOKASSA_TEST", "0") == "1",
        rk_hash=os.environ.get("ROBOKASSA_HASH", "md5").lower(),
        rk_receipt=os.environ.get("ROBOKASSA_RECEIPT", "1") == "1",
        rk_sno=os.environ.get("ROBOKASSA_SNO", "").strip(),
        order_start=int(os.environ.get("ORDER_START", "500000")),
        claim_code=os.environ.get("CLAIM_CODE", "").strip(),
    )
