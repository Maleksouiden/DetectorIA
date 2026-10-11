"""Fenêtre de bureau : la maquette HTML, reliée à l'analyse."""

from __future__ import annotations

import json
import os
import shutil
import threading
import traceback
from pathlib import Path

import webview

from detectoria.config import find_model, load_config, save_config
from detectoria.detector import Detector
from detectoria.pdf_analysis import analyse_pdf, output_path_for, write_reformulations
from detectoria.reformulate import Reformulator

UI = Path(__file__).resolve().parent / "ui" / "index.html"


def _gpu_label() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            return f"Accélération GPU (CUDA) active · {name}"
    except Exception:
        pass
    return "Calcul sur le processeur"


def _device_name(choice: str) -> str:
    if choice == "cpu":
        return "cpu"
    if choice == "cuda":
        return "cuda"
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _slug(hf_id: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in hf_id).strip("-").lower()[:48]


def _unique_id(base: str, models: list[dict]) -> str:
    used = {model.get("id") for model in models}
    if base not in used:
        return base
    number = 2
    while f"{base}-{number}" in used:
        number += 1
    return f"{base}-{number}"


def _one_path(value) -> str | None:
    if not value:
        return None
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else None
    return str(value)


class Api:
    def __init__(self):
        self.window = None
        self.cfg = load_config()
        self.cancel = threading.Event()
        self.busy = False
        self.pdf_path: str | None = None
        self.result: dict | None = None
        self.detector: Detector | None = None
        self.reformulator: Reformulator | None = None

    def initial_state(self) -> dict:
        current = find_model(self.cfg["detectors"], self.cfg.get("active_detector", ""))
        return {
            "gpu": _gpu_label(),
            "threshold": round(float(self.cfg["threshold"]) * 100),
            "reformulate": bool(self.cfg.get("reformulate_after_detection")),
            "models": [{"label": m["label"], "note": m.get("note", "")} for m in self.cfg["detectors"]],
            "active": current["label"] if current else "",
        }

    def choose_pdf(self):
        if self.window is None:
            return None
        picked = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("PDF (*.pdf)",),
        )
        path = _one_path(picked)
        if not path:
            return None
        return self.use_pdf(path)

    def use_pdf(self, path: str):
        if not path or not Path(path).is_file():
            return None
        self.pdf_path = path
        self.result = None
        return {"path": path, "name": Path(path).name, "size": Path(path).stat().st_size}

    def analyze(self, threshold: float, model_label: str, reformulate: bool) -> dict:
        if self.busy or not self.pdf_path:
            return {"ok": False}
        spec = self._detector_by_label(model_label)
        if spec is None:
            self._emit("showError", "Ajoutez un modèle de détection dans les réglages.")
            return {"ok": False}
        self.cfg["active_detector"] = spec["id"]
        self.cfg["threshold"] = min(0.99, max(0.50, float(threshold)))
        self.cfg["reformulate_after_detection"] = bool(reformulate)
        save_config(self.cfg)
        self._start(bool(reformulate), findings_only=False)
        return {"ok": True}

    def reformulate(self) -> dict:
        if self.busy or not (self.result and self.result.get("findings")):
            return {"ok": False}
        self._start(True, findings_only=True)
        return {"ok": True}

    def stop(self) -> None:
        if self.busy:
            self.cancel.set()
            self._emit("progress", 0, "Arrêt demandé…")

    def open_pdf(self) -> None:
        path = (self.result or {}).get("out_path")
        if path:
            os.startfile(path)  # noqa: S606

    def save_pdf(self) -> None:
        source = (self.result or {}).get("out_path")
        if not source or self.window is None:
            return
        dest = _one_path(self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=Path(source).name,
            file_types=("PDF (*.pdf)",),
        ))
        if not dest:
            return
        if not dest.lower().endswith(".pdf"):
            dest += ".pdf"
        shutil.copyfile(source, dest)
        self._emit("progress", 100, f"Copie enregistrée : {dest}")

    def export_report(self) -> None:
        if not self.result or self.window is None:
            return
        initial = "rapport_analyse_IA.txt"
        if self.pdf_path:
            initial = Path(self.pdf_path).stem + "_rapport_IA.txt"
        dest = _one_path(self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=initial,
            file_types=("Texte (*.txt)",),
        ))
        if not dest:
            return
        if not dest.lower().endswith(".txt"):
            dest += ".txt"
        Path(dest).write_text(self._report(), encoding="utf-8")
        self._emit("progress", 100, f"Rapport enregistré : {dest}")

    def settings_state(self) -> dict:
        active = find_model(self.cfg["reformulators"], self.cfg.get("active_reformulator", ""))
        return {
            "device": self.cfg.get("device", "auto"),
            "strong_prob": round(float(self.cfg["strong_prob"]) * 100),
            "batch_size": int(self.cfg["batch_size"]),
            "min_words": int(self.cfg["min_words"]),
            "target_words": int(self.cfg["target_words"]),
            "max_words": int(self.cfg["max_words"]),
            "reformulators": [m["label"] for m in self.cfg["reformulators"]],
            "active_reformulator": active["label"] if active else "",
        }

    def save_settings(self, data: dict) -> dict:
        try:
            self.cfg["device"] = data.get("device") if data.get("device") in ("auto", "cpu", "cuda") else "auto"
            self.cfg["strong_prob"] = float(data.get("strong_prob", 95)) / 100
            self.cfg["batch_size"] = int(data.get("batch_size", 8))
            self.cfg["min_words"] = int(data.get("min_words", 40))
            self.cfg["target_words"] = int(data.get("target_words", 170))
            self.cfg["max_words"] = int(data.get("max_words", 300))
            label = (data.get("reformulator") or "").strip()
            for model in self.cfg["reformulators"]:
                if model["label"] == label:
                    self.cfg["active_reformulator"] = model["id"]
            new_model = (data.get("new_model") or "").strip()
            if new_model:
                if "/" not in new_model:
                    return {"error": "L'identifiant Hugging Face doit ressembler à organisation/nom-du-modele."}
                model = {
                    "id": _unique_id(_slug(new_model), self.cfg["detectors"]),
                    "hf_id": new_model,
                    "label": new_model,
                    "note": "Modèle ajouté manuellement.",
                    "force_ai_label_id": None,
                }
                self.cfg["detectors"].append(model)
                self.cfg["active_detector"] = model["id"]
            save_config(self.cfg)
        except (TypeError, ValueError):
            return {"error": "Un des nombres n'est pas valide."}
        current = find_model(self.cfg["detectors"], self.cfg.get("active_detector", ""))
        return {
            "models": [{"label": m["label"], "note": m.get("note", "")} for m in self.cfg["detectors"]],
            "active": current["label"] if current else "",
        }

    def _detector_by_label(self, label: str) -> dict | None:
        for model in self.cfg["detectors"]:
            if model["label"] == label:
                return model
        return find_model(self.cfg["detectors"], self.cfg.get("active_detector", ""))

    def _start(self, reformulate: bool, findings_only: bool) -> None:
        self.busy = True
        self.cancel.clear()
        options = dict(self.cfg)
        active = find_model(self.cfg["detectors"], self.cfg.get("active_detector", "")) or {}
        reform_spec = dict(find_model(self.cfg["reformulators"], self.cfg.get("active_reformulator", "")) or {})
        threading.Thread(
            target=self._worker,
            args=(self.pdf_path, options, dict(active), reform_spec, reformulate, findings_only),
            daemon=True,
        ).start()

    def _worker(self, path, options, spec, reform_spec, reformulate, findings_only) -> None:
        try:
            cache = options.get("cache_dir") or ""
            if cache:
                Path(cache).mkdir(parents=True, exist_ok=True)
                os.environ["HF_HOME"] = cache
            if not findings_only:
                self._load_detector(spec, options)
                dest = output_path_for(path)
                result = analyse_pdf(
                    path,
                    self.detector,
                    options,
                    str(dest),
                    lambda done, total, msg: self._emit("progress", 100 * done / max(total, 1), msg),
                    self.cancel,
                )
            else:
                result = dict(self.result or {})
                result["cancelled"] = False
            if reformulate and result.get("findings") and not result.get("cancelled"):
                if not reform_spec:
                    raise RuntimeError("Aucun modèle de reformulation n'est configuré.")
                self._load_reformulator(reform_spec, options)
                total = len(result["findings"])
                for index, finding in enumerate(result["findings"], start=1):
                    if self.cancel.is_set():
                        result["cancelled"] = True
                        break
                    self._emit("progress", 100 * (index - 1) / total, f"Reformulation {index}/{total}…")
                    finding["reformulation"] = self.reformulator.paraphrase(finding["text"])
                if result.get("out_path"):
                    write_reformulations(result["out_path"], result["findings"])
            self.result = result
            if result.get("cancelled"):
                message = "Analyse interrompue."
            elif not result.get("analysed_words"):
                message = "Le PDF ne contient pas assez de texte sélectionnable."
            elif result.get("out_path"):
                message = "Analyse terminée. PDF surligné : " + Path(result["out_path"]).name
            else:
                message = "Analyse terminée."
            self._emit("showResult", self._public_result(result, message))
        except Exception as exc:  # noqa: BLE001
            self._emit("showError", f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
        finally:
            self.busy = False

    def _public_result(self, result: dict, message: str) -> dict:
        passages = []
        for item in result.get("passages") or result.get("findings") or []:
            passages.append({
                "page": item["page"],
                "prob": item["prob"],
                "words": item["words"],
                "text": item["text"],
                "reformulation": item.get("reformulation") or "",
                "flagged": bool(item.get("flagged", True)),
            })
        analysed = result.get("analysed_words") or 0
        return {
            "passages": passages,
            "analysed_words": analysed,
            "out_path": result.get("out_path"),
            "cancelled": bool(result.get("cancelled")),
            "calibration": result.get("calibration") or "",
            "french_warning": (result.get("french_ratio") or 1) < 0.08 and analysed > 0,
            "message": message,
        }

    def _load_detector(self, spec: dict, options: dict) -> None:
        same = (
            self.detector is not None
            and self.detector.spec.get("hf_id") == spec.get("hf_id")
            and self.detector.spec.get("force_ai_label_id") == spec.get("force_ai_label_id")
            and self.detector.batch_size == int(options["batch_size"])
            and self.detector.device == _device_name(options["device"])
        )
        if same:
            return
        if self.detector is not None:
            self.detector.close()
            self.detector = None
        self._emit("progress", 0, f"Chargement de {spec.get('label', spec.get('hf_id'))}…")
        self._emit("setIndeterminate", True)
        self.detector = Detector(
            spec,
            device=options["device"],
            batch_size=int(options["batch_size"]),
            log=lambda msg: self._emit("progress", 0, msg),
        )
        self._emit("setIndeterminate", False)

    def _load_reformulator(self, spec: dict, options: dict) -> None:
        same = (
            self.reformulator is not None
            and self.reformulator.spec.get("hf_id") == spec.get("hf_id")
            and self.reformulator.device == _device_name(options["device"])
        )
        if same:
            self.reformulator.spec = spec
            return
        if self.reformulator is not None:
            self.reformulator.close()
            self.reformulator = None
        self._emit("progress", 0, f"Chargement du reformulateur {spec.get('label', '')}…")
        self._emit("setIndeterminate", True)
        self.reformulator = Reformulator(spec, device=options["device"], log=lambda msg: self._emit("progress", 0, msg))
        self._emit("setIndeterminate", False)

    def _report(self) -> str:
        result = self.result or {}
        lines = [
            "Détecteur de texte IA — rapport",
            f"Fichier : {self.pdf_path or ''}",
            f"Seuil : {float(self.cfg['threshold']):.0%}",
            result.get("calibration") or "",
            result.get("verdict") or "",
            f"Mots signalés : {result.get('flagged_words', 0)}/{result.get('analysed_words', 0)} ({result.get('share', 0):.0%})",
            f"PDF surligné : {result.get('out_path') or ''}",
            "",
        ]
        for finding in result.get("findings") or []:
            lines.append(f"--- Page {finding['page']} · {finding['prob']:.0%} · {finding['words']} mots ---")
            lines.append(finding["text"])
            if finding.get("reformulation"):
                lines.append("")
                lines.append("Reformulation :")
                lines.append(finding["reformulation"])
            lines.append("")
        return "\n".join(lines)

    def _emit(self, name: str, *args) -> None:
        if self.window is None:
            return
        raw = json.dumps(list(args), ensure_ascii=False)
        raw = raw.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        self.window.evaluate_js(f"{name}.apply(null, {raw})")


def main() -> None:
    api = Api()
    window = webview.create_window(
        "Détecteur de texte IA",
        UI.as_uri(),
        js_api=api,
        width=1180,
        height=820,
        min_size=(960, 680),
        background_color="#EEF1F4",
        text_select=True,
    )
    api.window = window
    webview.start()
