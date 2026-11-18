"""Сохранённые пользовательские сценарии (отдельно от исходных данных)."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .provision import validate_capacity

SCENARIOS_PATH = config.RESULTS_DIR / "saved_scenarios.json"
_LOCK = threading.Lock()


def _load() -> list[dict[str, Any]]:
    if not SCENARIOS_PATH.exists():
        return []
    try:
        with SCENARIOS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list[dict[str, Any]]) -> None:
    with SCENARIOS_PATH.open("w", encoding="utf-8") as file:
        json.dump(items, file, ensure_ascii=False, indent=2)


def list_scenarios() -> list[dict[str, Any]]:
    with _LOCK:
        return _load()


def save_scenario(name: str, hospitals: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("Укажите название сценария")
    normalized = []
    for item in hospitals:
        normalized.append(
            {
                "block_id": int(item["block_id"]),
                "capacity": validate_capacity(item["capacity"]),
            }
        )
    if not normalized:
        raise ValueError("В сценарии нет ни одной больницы")

    record = {
        "id": uuid.uuid4().hex,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hospitals": normalized,
        "summary": summary,
    }
    with _LOCK:
        items = _load()
        items.insert(0, record)
        _save(items)
    return record


def delete_scenario(scenario_id: str) -> bool:
    with _LOCK:
        items = _load()
        filtered = [item for item in items if item.get("id") != scenario_id]
        if len(filtered) == len(items):
            return False
        _save(filtered)
        return True
