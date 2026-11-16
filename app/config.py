"""Alle Einstellungen kommen aus Umgebungsvariablen (nie im Code speichern!)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    app_password: str = field(default_factory=lambda: os.environ.get("APP_PASSWORD", ""))
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", ""))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "/tmp/autoshorts")))
    music_dir: Path = field(default_factory=lambda: Path(os.environ.get("MUSIC_DIR", "/app/music")))

    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 500))
    max_video_minutes: float = field(default_factory=lambda: _float("MAX_VIDEO_MINUTES", 30))
    daily_budget_usd: float = field(default_factory=lambda: _float("DAILY_BUDGET_USD", 2.0))
    retention_hours: float = field(default_factory=lambda: _float("RETENTION_HOURS", 6))
    min_clips: int = field(default_factory=lambda: _int("MIN_CLIPS", 3))
    max_clips: int = field(default_factory=lambda: _int("MAX_CLIPS", 5))
    clip_min_s: float = field(default_factory=lambda: _float("CLIP_MIN_SECONDS", 15))
    clip_max_s: float = field(default_factory=lambda: _float("CLIP_MAX_SECONDS", 60))

    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    claude_model: str = field(default_factory=lambda: os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"))
    # "openai" (Standard, wenn Schlüssel vorhanden) oder "none"
    transcribe_provider: str = field(default_factory=lambda: os.environ.get("TRANSCRIBE_PROVIDER", "auto"))
    language: str = field(default_factory=lambda: os.environ.get("VIDEO_LANGUAGE", "de"))

    # x264-Preset: schneller = größere Datei, weniger CPU
    x264_preset: str = field(default_factory=lambda: os.environ.get("X264_PRESET", "veryfast"))
    secure_cookies: bool = field(default_factory=lambda: os.environ.get("SECURE_COOKIES", "1") != "0")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    def use_openai(self) -> bool:
        if self.transcribe_provider == "none":
            return False
        return bool(self.openai_api_key)

    def use_claude(self) -> bool:
        return bool(self.anthropic_api_key) and os.environ.get("HIGHLIGHT_AI", "auto") != "off"


def load_settings() -> Settings:
    s = Settings()
    s.jobs_dir.mkdir(parents=True, exist_ok=True)
    return s
