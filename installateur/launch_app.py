"""Lance l'application installée. Le processus visible est ce programme, avec son icône."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def _install_dir() -> Path | None:
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(Path(local) / "DetectorIA")
    for folder in candidates:
        pythonw = folder / "venv" / "Scripts" / "pythonw.exe"
        app = folder / "app.py"
        if pythonw.is_file() and app.is_file():
            return folder
    return None


def _alert(message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Détecteur IA", 0x10)
    else:
        print(message, file=sys.stderr)


def main() -> None:
    folder = _install_dir()
    if folder is None:
        _alert("L'application n'est pas installée.\nLancez setup.exe pour l'installer.")
        return
    pythonw = folder / "venv" / "Scripts" / "pythonw.exe"
    app = folder / "app.py"
    log_dir = folder / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "lancement.log"
    with log_path.open("a", encoding="utf-8") as log:
        try:
            subprocess.Popen(
                [str(pythonw), str(app)],
                cwd=str(folder),
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except OSError as exc:
            _alert(f"Impossible de lancer l'application.\n{exc}")


if __name__ == "__main__":
    main()
