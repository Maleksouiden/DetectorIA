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
import time
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
    value = float(max(0, amount))
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            if unit == "o":
                return f"{int(value)} o"
            return f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{int(amount)} o"


class Reporter:
    """Avance la barre selon l'étape, puis selon les octets du fichier en cours."""

    def __init__(self, emit):
        self.emit = emit
        self.start = 0.0
        self.end = 1.0
        self.floor = 0.0
        self.single = False
        self.label = ""
        self.clock_total: int | None = None
        self.clock_t: float | None = None
        self.clock_base = 0
        self.shown = 0.0

    def stage(self, start: float, end: float, label: str, single: bool = False) -> None:
        self.start = start
        self.end = end
        self.floor = start
        self.shown = start
        self.single = single
        self.label = label
        self.clock_total = None
        self.clock_t = None
        self._show(start, label)

    def __call__(self, text: str) -> None:
        line = str(text).strip()
        if not line:
            return
        match = re.search(r"Progress\s+(\d+)\s+of\s+(\d+)", line)
        if match:
            self.file_progress(int(match.group(1)), int(match.group(2)))
            return
        self.emit(line)

    def file_progress(self, current: int, total: int) -> None:
        if total <= 0:
            return
        now = time.monotonic()
        if self.clock_total != total:
            self.floor = self.shown
            self.clock_total = total
            self.clock_t = now
            self.clock_base = current
        fraction = min(1.0, current / total)
        if self.single:
            percent = self.start + (self.end - self.start) * fraction
        else:
            percent = self.floor + (self.end - self.floor) * fraction * 0.8
        percent = max(self.shown, percent)
        self.shown = percent
        eta = ""
        elapsed = now - (self.clock_t or now)
        done = current - self.clock_base
        if done > 0 and elapsed >= 1 and current < total:
            eta = format_eta(elapsed * (total - current) / done)
        detail = f"{self.label} — {human_size(current)} / {human_size(total)}"
        if eta:
            detail += f" — il reste {eta}"
        self._show(percent, detail)

    def _show(self, percent: float, detail: str) -> None:
        self.emit(("progress", max(0.0, min(100.0, percent)), detail))


def _pip(python: Path, args: list[str]) -> list[str]:
    # --progress-bar est une option de « install », pas une option générale de pip.
    return [str(python), "-m", "pip", "--disable-pip-version-check", *args, "--progress-bar", "raw"]


def _run(cmd: list[str], reporter: Reporter) -> int:
    reporter("$ " + " ".join(cmd))
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
        reporter(line.rstrip("\n"))
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
            def hook(count, block, total, reporter=log):
                if hasattr(reporter, "file_progress"):
                    reporter.file_progress(count * block, total or 0)

            urllib.request.urlretrieve(url, dest, reporthook=hook)
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


def install(reporter: Reporter) -> Path:
    payload = _payload_dir()
    dest = _install_dir()
    reporter.stage(0, 8, "Copie des fichiers")
    _copy_payload(payload, dest, reporter)
    reporter.stage(8, 18, "Python", single=True)
    python = _ensure_python(reporter)
    venv_python = dest / "venv" / "Scripts" / "python.exe"
    reporter.stage(18, 24, "Environnement Python")
    if not venv_python.is_file():
        code = _run([str(python), "-m", "venv", str(dest / "venv")], reporter)
        if code != 0 or not venv_python.is_file():
            raise RuntimeError("La création de l'environnement Python a échoué.")
    reporter.stage(24, 32, "Mise à jour de pip")
    code = _run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], reporter)
    if code != 0:
        raise RuntimeError("La mise à jour de pip a échoué.")
    reporter.stage(32, 58, "Bibliothèques du programme")
    code = _run(_pip(venv_python, ["install", "-r", str(dest / "requirements.txt")]), reporter)
    if code != 0:
        raise RuntimeError("L'installation des bibliothèques a échoué.")
    reporter.stage(58, 96, "PyTorch", single=True)
    code = _run(
        _pip(venv_python, [
            "install", "torch",
            "--index-url", _torch_index(),
            "--extra-index-url", "https://pypi.org/simple",
        ]),
        reporter,
    )
    if code != 0:
        raise RuntimeError("L'installation de PyTorch a échoué.")
    icon = dest / "app.ico"
    exe = dest / "DetectorIA.exe"
    if not exe.is_file():
        raise RuntimeError("Le lanceur DetectorIA.exe est absent du paquet d'installation.")
    reporter.stage(96, 100, "Icônes du bureau et du menu Démarrer")
    _shortcut(_desktop_dir() / f"{APP_NAME}.lnk", exe, dest, icon)
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    _shortcut(start_menu / f"{APP_NAME}.lnk", exe, dest, icon)
    reporter.stage(100, 100, "Installation terminée")
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
        status = ttk.Frame(self)
        status.pack(fill="x", padx=16)
        self.detail = ttk.Label(status, text="En attente")
        self.detail.pack(side="left")
        self.pct = ttk.Label(status, text="0 %", width=8, anchor="e")
        self.pct.pack(side="right")
        self.bar = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.bar.pack(fill="x", padx=16, pady=(4, 0))
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
            "PyTorch est téléchargé ici. Les modèles d'IA se téléchargent au lancement de l'application.\n"
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
                    self.bar["value"] = 100
                    self.pct.config(text="100 %")
                    self.detail.config(text="Installation terminée")
                    self.install_btn.config(state="disabled")
                    self.open_btn.config(state="normal")
                    self._write("Vous pouvez lancer Détecteur IA depuis l'icône du bureau.")
                    messagebox.showinfo(
                        APP_NAME,
                        "Installation terminée.\nUne icône a été ajoutée sur le bureau.\n\n"
                        "Au premier lancement, les modèles d'IA se téléchargent dans l'application, "
                        "avec l'avancement et le temps restant.",
                    )
                elif isinstance(item, tuple) and item[0] == "progress":
                    percent = float(item[1])
                    self.bar["value"] = percent
                    self.pct.config(text=f"{percent:.0f} %")
                    self.detail.config(text=str(item[2]))
                elif isinstance(item, tuple) and item[0] == "done":
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
        self.bar["value"] = 0

        def worker():
            try:
                self.exe = install(Reporter(self.messages.put))
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
