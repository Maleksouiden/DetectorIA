"""Téléchargement des modèles, avec pourcentage et temps restant."""

from __future__ import annotations

import threading
import time

_LOCKS: dict[str, threading.Lock] = {}


def format_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 5:
        return "quelques secondes"
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        if rest and minutes < 10:
            return f"{minutes} min {rest:02d} s"
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def human_size(amount: float) -> str:
    value = float(amount)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            if unit == "o":
                return f"{int(value)} o"
            text = f"{value:.1f}".replace(".", ",")
            return f"{text} {unit}"
        value /= 1024
    return f"{int(amount)} o"


def ensure_repo(repo_id: str, cache_dir: str, on_progress) -> None:
    """Télécharge un dépôt Hugging Face. on_progress(fraction, courant, total, eta)."""
    lock = _LOCKS.setdefault(repo_id, threading.Lock())
    with lock:
        from huggingface_hub import snapshot_download

        clock = {"t": None, "base": 0.0, "last": 0.0}

        def callback(fraction: float, current: float, total: float, eta: str) -> None:
            on_progress(fraction, current, total, eta)

        bar = _byte_bar(callback, clock)
        kwargs = {"repo_id": repo_id, "tqdm_class": bar}
        if cache_dir:
            kwargs["cache_dir"] = cache_dir
        snapshot_download(**kwargs)


def repo_size(repo_id: str) -> int:
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo_id)
        return sum(int(item.size or 0) for item in info.siblings or [])
    except Exception:
        return 0


def _byte_bar(callback, clock: dict):
    from tqdm.auto import tqdm

    class Bar(tqdm):
        def __init__(self, *args, **kwargs):
            self._watch = kwargs.get("unit") == "B" and str(kwargs.get("desc", "")).startswith("Downloading")
            kwargs["disable"] = True
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            shown = super().update(n)
            if not self._watch:
                return shown
            now = time.monotonic()
            if now - clock["last"] < 0.3 and (self.total or 0) > (self.n or 0):
                return shown
            clock["last"] = now
            current = float(self.n or 0)
            total = float(self.total or 0)
            if clock["t"] is None:
                clock["t"] = now
                clock["base"] = current
            eta = ""
            done = current - clock["base"]
            elapsed = now - clock["t"]
            if done > 0 and elapsed >= 1 and total > current:
                eta = format_eta(elapsed * (total - current) / done)
            fraction = current / total if total else 0.0
            callback(fraction, current, total, eta)
            return shown

    return Bar
