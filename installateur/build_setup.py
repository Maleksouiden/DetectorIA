"""Construit setup.exe à la racine du dépôt."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = Path(__file__).resolve().parent
PAYLOAD = PACKAGING / "payload"
ICON_SOURCE = PACKAGING / "icon-source.jpg"


def _icon() -> Path:
    from PIL import Image

    dest = PACKAGING / "app.ico"
    source = ICON_SOURCE if ICON_SOURCE.is_file() else PACKAGING / "app.ico"
    if source.suffix.lower() == ".ico" and source.is_file() and dest.is_file():
        return dest
    image = Image.open(source).convert("RGBA")
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    image.save(dest, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    return dest


def _pyinstaller(args: list[str]) -> None:
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", *args]
    print(" ".join(command))
    subprocess.run(command, check=True, cwd=ROOT)


def main() -> None:
    icon = _icon()
    for folder in (PACKAGING / "build", PACKAGING / "dist", PAYLOAD):
        if folder.exists():
            shutil.rmtree(folder)
    PAYLOAD.mkdir(parents=True)
    _pyinstaller([
        "--icon", str(icon),
        "--name", "DetectorIA",
        "--distpath", str(PACKAGING / "dist"),
        "--workpath", str(PACKAGING / "build" / "launcher"),
        "--specpath", str(PACKAGING / "build"),
        str(PACKAGING / "launch_app.py"),
    ])
    for name in ("app.py", "config.json", "requirements.txt", "LICENSE", "DROITS.md"):
        shutil.copy2(ROOT / name, PAYLOAD / name)
    shutil.copytree(
        ROOT / "detectoria",
        PAYLOAD / "detectoria",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(PACKAGING / "dist" / "DetectorIA.exe", PAYLOAD / "DetectorIA.exe")
    shutil.copy2(icon, PAYLOAD / "app.ico")
    _pyinstaller([
        "--icon", str(icon),
        "--name", "setup",
        "--distpath", str(PACKAGING / "dist"),
        "--workpath", str(PACKAGING / "build" / "setup"),
        "--specpath", str(PACKAGING / "build"),
        "--add-data", f"{PAYLOAD};payload",
        str(PACKAGING / "setup_app.py"),
    ])
    shutil.copy2(PACKAGING / "dist" / "setup.exe", ROOT / "setup.exe")
    print(f"Écrit : {ROOT / 'setup.exe'}")


if __name__ == "__main__":
    main()
