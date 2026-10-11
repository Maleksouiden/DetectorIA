"""Vérifie la branche main sur GitHub et met à jour l'application installée."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

REPO = "Maleksouiden/DetectorIA"
BRANCH = "main"
COMMIT_FILE = "installed.commit"
KEEP_FILES = ("app.py", "requirements.txt", "LICENSE", "DROITS.md", "README.md")


def installed_root() -> Path | None:
    root = Path(__file__).resolve().parent.parent
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    expected = (Path(local) / "DetectorIA").resolve()
    if root.resolve() != expected:
        return None
    pythonw = root / "venv" / "Scripts" / "pythonw.exe"
    if pythonw.is_file() and (root / "app.py").is_file():
        return root
    return None


def check_and_install(on_progress=lambda *_a: None) -> str:
    """Retourne 'updated', 'current' ou 'skipped'."""
    root = installed_root()
    if root is None:
        return "skipped"
    on_progress(5, "Recherche d'une mise à jour…")
    remote = _remote_commit()
    local = _read_commit(root)
    if local == remote:
        return "current"
    on_progress(15, "Téléchargement de la mise à jour…")
    blob = _download_zip(lambda fraction: on_progress(15 + fraction * 55, "Téléchargement de la mise à jour…"))
    on_progress(75, "Installation de la mise à jour…")
    requirements_changed = _apply_zip(root, blob)
    if requirements_changed:
        on_progress(85, "Mise à jour des bibliothèques…")
        _pip_install(root)
    _write_commit(root, remote)
    on_progress(100, "Mise à jour installée. Redémarrage…")
    return "updated"


def _remote_commit() -> str:
    url = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
    request = urllib.request.Request(url, headers={"User-Agent": "DetectorIA", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    sha = str(payload.get("sha") or "")
    if len(sha) < 7:
        raise RuntimeError("La version distante est illisible.")
    return sha


def _download_zip(on_fraction) -> bytes:
    url = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "DetectorIA"})
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        chunks = []
        received = 0
        while True:
            block = response.read(1024 * 256)
            if not block:
                break
            chunks.append(block)
            received += len(block)
            if total:
                on_fraction(min(1.0, received / total))
        return b"".join(chunks)


def _apply_zip(root: Path, blob: bytes) -> bool:
    prefix = f"{REPO.split('/')[-1]}-{BRANCH}/"
    old_requirements = _text(root / "requirements.txt")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            relative = name[len(prefix):]
            if not _wanted(relative):
                continue
            destination = (root / relative).resolve()
            if root.resolve() not in destination.parents and destination != root.resolve():
                continue
            if relative == "config.json" and destination.is_file():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    return _text(root / "requirements.txt") != old_requirements


def _wanted(relative: str) -> bool:
    if relative in KEEP_FILES or relative == "config.json":
        return True
    return relative.startswith("detectoria/") and ".." not in relative.split("/")


def _pip_install(root: Path) -> None:
    python = root / "venv" / "Scripts" / "python.exe"
    if not python.is_file():
        return
    subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")],
        cwd=str(root),
        check=False,
        creationflags=0x08000000 if os.name == "nt" else 0,
    )


def _read_commit(root: Path) -> str:
    path = root / COMMIT_FILE
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _write_commit(root: Path, sha: str) -> None:
    (root / COMMIT_FILE).write_text(sha + "\n", encoding="utf-8")


def _text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")
