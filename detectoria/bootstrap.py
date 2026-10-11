"""Installe les bibliothèques manquantes au premier lancement."""

from __future__ import annotations

import importlib.util
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

APP_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS = APP_DIR / "requirements.txt"

CHECKS = (
    ("fitz", "pymupdf"),
    ("transformers", "transformers"),
    ("sentencepiece", "sentencepiece"),
    ("torch", "torch"),
    ("webview", "pywebview"),
)


def missing_packages() -> list[str]:
    missing = []
    for module, package in CHECKS:
        if importlib.util.find_spec(module) is None:
            missing.append(package)
    return missing


def _pip_commands(packages: list[str]) -> list[list[str]]:
    commands = []
    others = [name for name in packages if name != "torch"]
    if others and REQUIREMENTS.is_file():
        commands.append([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    elif others:
        commands.append([sys.executable, "-m", "pip", "install", *others])
    if "torch" in packages:
        commands.append([
            sys.executable, "-m", "pip", "install", "torch",
            "--index-url", _torch_index(),
            "--extra-index-url", "https://pypi.org/simple",
        ])
    return commands


def _torch_index() -> str:
    """Le wheel PyPI est souvent limité au processeur. Le GPU passe par l'index PyTorch."""
    if shutil.which("nvidia-smi") is None:
        return "https://download.pytorch.org/whl/cpu"
    try:
        out = subprocess.check_output(["nvidia-smi"], text=True, errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "https://download.pytorch.org/whl/cu126"
    match = re.search(r"CUDA Version:\s+(\d+)\.(\d+)", out)
    if match and int(match.group(1)) >= 13:
        return "https://download.pytorch.org/whl/cu130"
    return "https://download.pytorch.org/whl/cu126"


def ensure_dependencies() -> None:
    packages = missing_packages()
    if not packages:
        return

    root = tk.Tk()
    root.withdraw()
    agreed = messagebox.askyesno(
        "Installation",
        "Il manque des composants pour lancer l'application :\n\n"
        + "\n".join(f"• {name}" for name in packages)
        + "\n\nPyTorch et les modèles sont volumineux "
        "(plusieurs centaines de Mo, parfois plus d'1 Go).\n"
        "Les télécharger maintenant ?",
    )
    root.destroy()
    if not agreed:
        raise SystemExit(0)

    _run_install(_pip_commands(packages))
    still = missing_packages()
    if still:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Installation incomplète",
            "Ces composants n'ont pas pu être installés :\n"
            + "\n".join(still)
            + "\n\nRelancez l'application, ou installez-les avec :\n"
            f"{sys.executable} -m pip install -r requirements.txt",
        )
        root.destroy()
        raise SystemExit(1)


def _run_install(commands: list[list[str]]) -> None:
    win = tk.Tk()
    win.title("Téléchargement des composants")
    win.geometry("640x360")
    ttk.Label(win, text="Installation en cours. Cela peut prendre plusieurs minutes.").pack(anchor="w", padx=12, pady=8)
    bar = ttk.Progressbar(win, mode="indeterminate")
    bar.pack(fill="x", padx=12)
    bar.start(12)
    text = tk.Text(win, height=16, wrap="word")
    text.pack(fill="both", expand=True, padx=12, pady=8)
    messages: queue.Queue = queue.Queue()

    def worker():
        try:
            for cmd in commands:
                messages.put("$ " + " ".join(cmd) + "\n")
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    messages.put(line)
                code = proc.wait()
                if code != 0:
                    messages.put(f"\nÉchec (code {code}).\n")
                    messages.put(("done", code))
                    return
            messages.put(("done", 0))
        except Exception as exc:  # noqa: BLE001
            messages.put(f"\n{type(exc).__name__}: {exc}\n")
            messages.put(("done", 1))

    threading.Thread(target=worker, daemon=True).start()
    finished = {"code": None}

    def poll():
        try:
            while True:
                item = messages.get_nowait()
                if isinstance(item, tuple) and item[0] == "done":
                    finished["code"] = item[1]
                    bar.stop()
                    win.destroy()
                    return
                text.insert("end", item)
                text.see("end")
        except queue.Empty:
            pass
        win.after(80, poll)

    win.after(80, poll)
    win.mainloop()
