"""Chargement et enregistrement des réglages (config.json)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"

DEFAULTS: dict = {
    "threshold": 0.80,
    "strong_prob": 0.95,
    "min_words": 40,
    "target_words": 170,
    "max_words": 300,
    "batch_size": 8,
    "device": "auto",
    "cache_dir": "",
    "color_suspect": [1.0, 0.72, 0.25],
    "color_strong": [1.0, 0.45, 0.45],
    "highlight_opacity": 0.45,
    "active_detector": "camemberta",
    "active_reformulator": "flan-t5-base",
    "reformulate_after_detection": False,
    "detectors": [],
    "reformulators": [],
}


def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_PATH
    data = deepcopy(DEFAULTS)
    if path.is_file():
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            data.update(raw)
    data["detectors"] = [m for m in data.get("detectors") or [] if _valid_model(m)]
    data["reformulators"] = [m for m in data.get("reformulators") or [] if _valid_model(m)]
    _clamp(data)
    return data


def save_config(data: dict, path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    _clamp(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_model(models: list[dict], model_id: str) -> dict | None:
    for model in models:
        if model.get("id") == model_id:
            return model
    return models[0] if models else None


def _valid_model(model: object) -> bool:
    return isinstance(model, dict) and bool(model.get("id")) and bool(model.get("hf_id"))


def _clamp(data: dict) -> None:
    data["threshold"] = min(0.99, max(0.50, float(data.get("threshold", 0.8))))
    data["strong_prob"] = min(0.99, max(0.80, float(data.get("strong_prob", 0.95))))
    data["min_words"] = max(10, int(data.get("min_words", 40)))
    data["target_words"] = max(data["min_words"], int(data.get("target_words", 170)))
    data["max_words"] = max(data["target_words"], int(data.get("max_words", 300)))
    data["batch_size"] = min(32, max(1, int(data.get("batch_size", 8))))
    if data.get("device") not in ("auto", "cpu", "cuda"):
        data["device"] = "auto"
    data["highlight_opacity"] = min(1.0, max(0.15, float(data.get("highlight_opacity", 0.45))))
