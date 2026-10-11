"""Installe Détecteur IA : Python, bibliothèques, lanceur et icônes."""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

CREATE_NO_WINDOW = 0x08000000
APP_NAME = "Détecteur IA"
PYTHON_INSTALLERS = (
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe",
    "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe",
)


def _payload_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "payload"
    return Path(__file__).resolve().parent / "payload"


def _install_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "DetectorIA"


def _no_window_flags() -> int:
    return CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _python_ok(path: Path) -> bool:
    if "WindowsApps" in path.parts:
        return False
    try:
        out = subprocess.check_output(
            [str(path), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            text=True,
            timeout=30,
            creationflags=_no_window_flags(),
        ).strip()
        major, minor = (int(part) for part in out.split("."))
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return (major, minor) >= (3, 10)


def find_python() -> Path | None:
    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA", "")
    versions = ("314", "313", "312", "311", "310")
    for version in versions:
        candidates.append(Path(local) / "Programs" / "Python" / f"Python{version}" / "python.exe")
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_name, "")
        if root:
            for version in versions:
                candidates.append(Path(root) / f"Python{version}" / "python.exe")
    launcher = shutil.which("py")
    if launcher:
        try:
            out = subprocess.check_output(
                [launcher, "-3", "-c", "import sys; print(sys.executable)"],
                text=True,
                timeout=30,
                creationflags=_no_window_flags(),
            ).strip()
            if out:
                candidates.insert(0, Path(out))
        except (OSError, subprocess.SubprocessError):
            pass
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        if _python_ok(path):
            return path
    return None


def _torch_index() -> str:
    if shutil.which("nvidia-smi") is None:
        return "https://download.pytorch.org/whl/cpu"
    try:
        out = subprocess.check_output(
            ["nvidia-smi"],
            text=True,
            errors="replace",
            timeout=20,
            creationflags=_no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return "https://download.pytorch.org/whl/cu126"
    match = re.search(r"CUDA Version:\s+(\d+)\.(\d+)", out)
    if match and int(match.group(1)) >= 13:
        return "https://download.pytorch.org/whl/cu130"
    return "https://download.pytorch.org/whl/cu126"


def _run(cmd: list[str], log) -> int:
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_no_window_flags(),
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip("\n"))
    return proc.wait()


def _copy_payload(source: Path, dest: Path, log) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"Fichiers d'installation introuvables : {source}")
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif item.name == "config.json" and target.is_file():
            log("Le fichier de réglages déjà présent est conservé.")
        else:
            shutil.copy2(item, target)
        log(f"Copié : {item.name}")


def _download_python(log) -> Path:
    last_error = "Le téléchargement a échoué."
    for url in PYTHON_INSTALLERS:
        name = url.rsplit("/", 1)[-1]
        dest = Path(tempfile.gettempdir()) / name
        log(f"Téléchargement de Python : {name}")
        try:
            urllib.request.urlretrieve(url, dest)
        except OSError as exc:
            last_error = str(exc)
            log(last_error)
            continue
        log("Installation de Python pour l'utilisateur en cours…")
        code = _run(
            [
                str(dest),
                "/quiet",
                "InstallAllUsers=0",
                "PrependPath=1",
                "Include_pip=1",
                "Include_test=0",
                "Shortcuts=0",
            ],
            log,
        )
        if code != 0:
            last_error = f"L'installateur Python s'est arrêté avec le code {code}."
            log(last_error)
            continue
        found = find_python()
        if found:
            return found
        last_error = "Python a été installé mais reste introuvable."
    raise RuntimeError(last_error)


def _ensure_python(log) -> Path:
    found = find_python()
    if found:
        log(f"Python trouvé : {found}")
        return found
    winget = shutil.which("winget")
    if winget:
        log("Python est absent. Installation avec winget…")
        _run(
            [
                winget, "install", "-e", "--id", "Python.Python.3.12",
                "--scope", "user",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            log,
        )
        found = find_python()
        if found:
            log(f"Python trouvé : {found}")
            return found
    log("Téléchargement de Python depuis python.org…")
    return _download_python(log)


def _shortcut(lnk: Path, target: Path, workdir: Path, icon: Path) -> None:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut({_ps(str(lnk))})
$shortcut.TargetPath = {_ps(str(target))}
$shortcut.WorkingDirectory = {_ps(str(workdir))}
$shortcut.IconLocation = {_ps(str(icon))}
$shortcut.Description = {_ps(APP_NAME)}
$shortcut.Save()
"""
    path = Path(tempfile.gettempdir()) / "detectoria-shortcut.ps1"
    path.write_text(script, encoding="utf-8-sig")
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)],
        check=True,
        creationflags=_no_window_flags(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _desktop_dir() -> Path:
    import ctypes

    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buffer) == 0 and buffer.value:
        return Path(buffer.value)
    return Path.home() / "Desktop"


def install(log) -> Path:
    payload = _payload_dir()
    dest = _install_dir()
    log("Copie des fichiers…")
    _copy_payload(payload, dest, log)
    python = _ensure_python(log)
    venv_python = dest / "venv" / "Scripts" / "python.exe"
    if not venv_python.is_file():
        log("Création de l'environnement Python…")
        code = _run([str(python), "-m", "venv", str(dest / "venv")], log)
        if code != 0 or not venv_python.is_file():
            raise RuntimeError("La création de l'environnement Python a échoué.")
    log("Installation des bibliothèques. Cette étape peut durer plusieurs minutes.")
    code = _run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], log)
    if code != 0:
        raise RuntimeError("La mise à jour de pip a échoué.")
    code = _run([str(venv_python), "-m", "pip", "install", "-r", str(dest / "requirements.txt")], log)
    if code != 0:
        raise RuntimeError("L'installation des bibliothèques a échoué.")
    code = _run(
        [
            str(venv_python), "-m", "pip", "install", "torch",
            "--index-url", _torch_index(),
            "--extra-index-url", "https://pypi.org/simple",
        ],
        log,
    )
    if code != 0:
        raise RuntimeError("L'installation de PyTorch a échoué.")
    icon = dest / "app.ico"
    exe = dest / "DetectorIA.exe"
    if not exe.is_file():
        raise RuntimeError("Le lanceur DetectorIA.exe est absent du paquet d'installation.")
    log("Création des icônes…")
    _shortcut(_desktop_dir() / f"{APP_NAME}.lnk", exe, dest, icon)
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    _shortcut(start_menu / f"{APP_NAME}.lnk", exe, dest, icon)
    log("Installation terminée.")
    return exe


class Setup(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Installer {APP_NAME}")
        self.geometry("720x480")
        self.minsize(640, 420)
        self.messages: queue.Queue = queue.Queue()
        self.exe: Path | None = None
        header = ttk.Frame(self)
        header.pack(fill="x", padx=16, pady=(16, 8))
        ttk.Label(header, text=APP_NAME, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="Installe le programme, les bibliothèques, puis une icône sur le bureau et dans le menu Démarrer.",
            wraplength=660,
        ).pack(anchor="w", pady=(4, 0))
        self.log = tk.Text(self, height=16, wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=16, pady=8)
        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.pack(fill="x", padx=16)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=16, pady=12)
        self.install_btn = ttk.Button(buttons, text="Installer", command=self.start)
        self.install_btn.pack(side="left")
        self.open_btn = ttk.Button(buttons, text="Lancer l'application", command=self.open_app, state="disabled")
        self.open_btn.pack(side="left", padx=8)
        ttk.Button(buttons, text="Fermer", command=self.destroy).pack(side="right")
        self.after(80, self._poll)
        self._write(
            "L'original de vos documents n'est jamais modifié.\n"
            "PyTorch et les modèles peuvent peser plus d'un gigaoctet.\n"
            "L'usage du logiciel relève de votre responsabilité (voir DROITS.md).\n"
        )

    def _write(self, text: str) -> None:
        self.log.insert("end", text if text.endswith("\n") else text + "\n")
        self.log.see("end")

    def _poll(self) -> None:
        try:
            while True:
                item = self.messages.get_nowait()
                if item == ("done", 0):
                    self.bar.stop()
                    self.install_btn.config(state="disabled")
                    self.open_btn.config(state="normal")
                    self._write("Vous pouvez lancer Détecteur IA depuis l'icône du bureau.")
                    messagebox.showinfo(APP_NAME, "Installation terminée.\nUne icône a été ajoutée sur le bureau.")
                elif isinstance(item, tuple) and item[0] == "done":
                    self.bar.stop()
                    self.install_btn.config(state="normal")
                    messagebox.showerror(APP_NAME, item[1])
                else:
                    self._write(str(item))
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def start(self) -> None:
        agreed = messagebox.askyesno(
            APP_NAME,
            "Installer Détecteur IA maintenant ?\n\n"
            "Le programme télécharge Python s'il manque, puis les bibliothèques "
            "(souvent plus d'un gigaoctet).",
        )
        if not agreed:
            return
        self.install_btn.config(state="disabled")
        self.bar.start(12)

        def worker():
            try:
                self.exe = install(self.messages.put)
                self.messages.put(("done", 0))
            except Exception as exc:  # noqa: BLE001
                self.messages.put(("done", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def open_app(self) -> None:
        exe = self.exe or (_install_dir() / "DetectorIA.exe")
        if exe.is_file():
            subprocess.Popen([str(exe)], cwd=str(exe.parent))


def main() -> None:
    Setup().mainloop()


if __name__ == "__main__":
    main()
