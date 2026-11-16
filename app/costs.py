"""Einfacher Kostenzähler mit Tageslimit (geschätzte API-Kosten)."""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

from app.errors import UserError

WHISPER_USD_PER_MIN = 0.006
_lock = threading.Lock()


def _today() -> str:
    return dt.date.today().isoformat()


class CostTracker:
    def __init__(self, path: Path, daily_budget: float):
        self.path = path
        self.daily_budget = daily_budget

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def spent_today(self) -> float:
        return float(self._load().get(_today(), 0.0))

    def check(self, estimate: float) -> None:
        spent = self.spent_today()
        if spent + estimate > self.daily_budget + 1e-9:
            raise UserError(
                f"Tageslimit erreicht: heute bereits ca. {spent:.2f} USD verbraucht, dieses Video "
                f"würde ca. {estimate:.2f} USD kosten (Limit {self.daily_budget:.2f} USD). "
                "Bitte morgen erneut versuchen oder DAILY_BUDGET_USD erhöhen.")

    def add(self, usd: float) -> None:
        if usd <= 0:
            return
        with _lock:
            data = self._load()
            data[_today()] = round(float(data.get(_today(), 0.0)) + usd, 6)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data))


def whisper_cost(duration_s: float) -> float:
    return duration_s / 60.0 * WHISPER_USD_PER_MIN
