"""Environment-backed configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader so local runs don't need extra dependencies."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session: str
    db_path: str

    entry_delay_sec: int
    slippage: float
    tp_multiple: float
    sl_drop: float
    horizon_hours: int

    window_days: int
    min_calls: int

    report_chat: str
    report_hour_utc: int
    max_joins_per_day: int

    @property
    def horizon_sec(self) -> int:
        return self.horizon_hours * 3600


def load_config() -> Config:
    _load_dotenv()
    db_path = os.environ.get("DB_PATH", "./data/tgfinder.db")
    Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return Config(
        api_id=_i("TG_API_ID", 0),
        api_hash=os.environ.get("TG_API_HASH", ""),
        session=os.environ.get("TG_SESSION", ""),
        db_path=db_path,
        entry_delay_sec=_i("SIM_ENTRY_DELAY_SEC", 60),
        slippage=_f("SIM_SLIPPAGE", 0.03),
        tp_multiple=_f("SIM_TP_MULTIPLE", 2.0),
        sl_drop=_f("SIM_SL_DROP", 0.5),
        horizon_hours=_i("SIM_HORIZON_HOURS", 24),
        window_days=_i("SCORE_WINDOW_DAYS", 30),
        min_calls=_i("SCORE_MIN_CALLS", 5),
        report_chat=os.environ.get("REPORT_CHAT", "me"),
        report_hour_utc=_i("REPORT_HOUR_UTC", 6),
        max_joins_per_day=_i("MAX_JOINS_PER_DAY", 8),
    )
