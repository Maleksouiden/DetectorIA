"""Fenêtre principale, réglages et rapport."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from detectoria import __version__
from detectoria.config import CONFIG_PATH, find_model, load_config, save_config
from detectoria.detector import Detector
from detectoria.pdf_analysis import analyse_document, output_path_for, write_reformulations
from detectoria.reformulate import Reformulator

HELP = """\
Choisir un document
    Ouvre l'explorateur (PDF ou Word .docx). Le fichier d'origine n'est jamais modifié.

Seuil
    80 % par défaut. Plus il est haut, moins il y a de faux positifs.
    Orange = au-dessus du seuil. Rouge = au moins le seuil « très probable »
    (95 % par défaut).

Analyser
    Le texte est découpé en passages d'environ 170 mots, de préférence
    en fin de paragraphe. Les titres, en-têtes, pieds de page, sommaires
    et tableaux de chiffres sont ignorés.
    Une copie nom_analyse_IA.pdf ou nom_analyse_IA.docx est créée, avec les passages surlignés.
    Dans Word, le jaune correspond au seuil, le rouge au seuil « très probable ».
    Les reformulations sont ajoutées à la fin de la copie Word.
    Le résumé indique le pourcentage de mots signalés.

Langue
    Si le document ne semble pas être en français, un avertissement s'affiche.
    Le modèle CamemBERTa est fait pour le français.

Calibrage
    Au chargement, le programme vérifie qu'il lit bien la classe « IA »
    sur 3 textes IA et 3 textes humains. Le score des textes IA doit être
    nettement plus haut. S'il est inversé, la classe est corrigée toute seule.

Modèles de détection
    • CamemBERTa (Inria) : le plus adapté au français.
    • XLM-RoBERTa (Inria) : français et anglais, si le PDF est mélangé.
    Vous pouvez en ajouter d'autres (identifiant Hugging Face) dans Réglages.
    Ils sont téléchargés au premier usage.

Reformulation
    Après la détection, les passages signalés peuvent être réécrits par un
    modèle de paraphrase français (CATIE Flan-T5). La version « large » est
    un peu meilleure et beaucoup plus lourde.
    Ce n'est qu'une proposition de formulation : elle ne prouve pas qu'un
    texte est humain, et elle ne doit pas servir à dissimuler un texte généré.

Aucun détecteur n'est fiable à 100 %. Ne l'utilisez pas comme seule preuve.
"""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.title("Détecteur de texte IA — Français")
        self.geometry("980x720")
        self.minsize(820, 560)
        self.pdf_path: str | None = None
        self.out_path: str | None = None
        self.findings: list[dict] = []
        self.result: dict | None = None
        self.detector: Detector | None = None
        self.reformulator: Reformulator | None = None
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False

        self._style()
        self._menu()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    def _style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TButton", padding=(10, 6))
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Muted.TLabel", foreground="#5c6570")
        style.configure("Warn.TLabel", foreground="#8a4b08")
        style.configure("Status.TLabel", foreground="#243140")

    def _menu(self) -> None:
        bar = tk.Menu(self)
        file_menu = tk.Menu(bar, tearoff=0)
        file_menu.add_command(label="Choisir un document…", command=self.choose)
        file_menu.add_command(label="Ouvrir la copie surlignée", command=self.open_out)
        file_menu.add_command(label="Enregistrer le PDF sous…", command=self.save_as)
        file_menu.add_command(label="Exporter le rapport…", command=self.export_report)
        file_menu.add_separator()
        file_menu.add_command(label="Quitter", command=self._on_close)
        bar.add_cascade(label="Fichier", menu=file_menu)

        run_menu = tk.Menu(bar, tearoff=0)
        run_menu.add_command(label="Analyser", command=self.run)
        run_menu.add_command(label="Reformuler les passages signalés", command=self.reformulate_only)
        run_menu.add_command(label="Arrêter", command=self.stop)
        bar.add_cascade(label="Analyse", menu=run_menu)

        bar.add_command(label="Réglages", command=self.open_settings)
        help_menu = tk.Menu(bar, tearoff=0)
        help_menu.add_command(label="Mode d'emploi", command=self.show_help)
        help_menu.add_command(label="À propos", command=self.show_about)
        bar.add_cascade(label="Aide", menu=help_menu)
        self.config(menu=bar)

    def _build(self) -> None:
        pad = {"padx": 14, "pady": 4}
        header = ttk.Frame(self)
        header.pack(fill="x", padx=14, pady=(12, 0))
        ttk.Label(header, text="Détecteur de texte IA", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="PDF ou Word en français · surlignage dans une copie · modèles configurables",
            style="Muted.TLabel",
        ).pack(anchor="w")

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Button(top, text="Choisir un document…", command=self.choose).pack(side="left")
        self.file_lbl = ttk.Label(top, text="Aucun fichier sélectionné", style="Muted.TLabel")
        self.file_lbl.pack(side="left", padx=12)

        models = ttk.Frame(self)
        models.pack(fill="x", **pad)
        ttk.Label(models, text="Modèle :").pack(side="left")
        self.detector_var = tk.StringVar()
        self.detector_box = ttk.Combobox(models, textvariable=self.detector_var, state="readonly", width=46)
        self.detector_box.pack(side="left", padx=8)
        self.detector_box.bind("<<ComboboxSelected>>", lambda _e: self._show_model_note())
        self._fill_detector_box()

        self.model_note = ttk.Label(self, text="", style="Muted.TLabel", wraplength=920, justify="left")
        self.model_note.pack(fill="x", padx=14)
        self._show_model_note()

        opt = ttk.Frame(self)
        opt.pack(fill="x", **pad)
        ttk.Label(opt, text="Seuil de détection :").pack(side="left")
        self.thr = tk.DoubleVar(value=float(self.cfg["threshold"]))
        ttk.Scale(
            opt, from_=0.50, to=0.99, variable=self.thr, length=240,
            command=lambda _v=None: self.thr_lbl.config(text=f"{self.thr.get():.0%}"),
        ).pack(side="left", padx=8)
        self.thr_lbl = ttk.Label(opt, text=f"{self.thr.get():.0%}", width=5)
        self.thr_lbl.pack(side="left")
        ttk.Label(opt, text="Plus il est haut, moins il y a de faux positifs.", style="Muted.TLabel").pack(side="left", padx=8)

        self.reform_var = tk.BooleanVar(value=bool(self.cfg.get("reformulate_after_detection")))
        ttk.Checkbutton(
            self,
            text="Reformuler les passages signalés après l'analyse (télécharge le modèle de paraphrase au premier usage)",
            variable=self.reform_var,
        ).pack(anchor="w", padx=14, pady=2)

        act = ttk.Frame(self)
        act.pack(fill="x", **pad)
        self.run_btn = ttk.Button(act, text="Analyser", command=self.run, state="disabled")
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(act, text="Arrêter", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.reform_btn = ttk.Button(act, text="Reformuler", command=self.reformulate_only, state="disabled")
        self.reform_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(act, text="Ouvrir la copie surlignée", command=self.open_out, state="disabled")
        self.open_btn.pack(side="left", padx=6)
        self.save_btn = ttk.Button(act, text="Enregistrer sous…", command=self.save_as, state="disabled")
        self.save_btn.pack(side="left")
        self.report_btn = ttk.Button(act, text="Exporter le rapport…", command=self.export_report, state="disabled")
        self.report_btn.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", padx=14, pady=4)
        self.status = ttk.Label(self, text="Prêt. Choisissez un PDF ou un document Word.", style="Status.TLabel")
        self.status.pack(fill="x", padx=14)
        self.summary = ttk.Label(self, text="", font=("Segoe UI", 11, "bold"), wraplength=940, justify="left")
        self.summary.pack(fill="x", padx=14, pady=(8, 2))

        book = ttk.Notebook(self)
        book.pack(fill="both", expand=True, padx=14, pady=6)
        self.tree = self._make_tree(book, ("page", "prob", "words", "extrait"), ("Page", "Probabilité IA", "Mots", "Passage signalé"), (70, 120, 70, 640))
        self.reform_tree = self._make_tree(
            book,
            ("page", "prob", "reform"),
            ("Page", "Probabilité IA", "Reformulation"),
            (70, 120, 720),
        )
        book.add(self.tree.master, text="Passages signalés")
        book.add(self.reform_tree.master, text="Reformulations")
        self.tree.bind("<Double-1>", lambda _e: self._open_detail(self.tree))
        self.reform_tree.bind("<Double-1>", lambda _e: self._open_detail(self.reform_tree))

        ttk.Label(
            self,
            text="Indicatif uniquement : un détecteur se trompe, surtout sur un texte très formel ou un modèle récent. "
                 "La reformulation est une autre façon d'écrire le passage, pas une preuve et pas un moyen de masquer l'origine du texte.",
            style="Warn.TLabel",
            wraplength=940,
            justify="left",
        ).pack(fill="x", padx=14, pady=(0, 4))
        self.calib = ttk.Label(self, text="Calibrage : en attente du premier chargement du modèle.", style="Muted.TLabel")
        self.calib.pack(fill="x", padx=14, pady=(0, 10))

    def _make_tree(self, notebook, columns, headings, widths):
        frame = ttk.Frame(notebook)
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        for col, title, width in zip(columns, headings, widths):
            tree.heading(col, text=title, command=lambda c=col, t=tree: self._sort_tree(t, c))
            tree.column(col, width=width, anchor="center" if col != "extrait" and col != "reform" else "w", stretch=(col in ("extrait", "reform")))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        tree.tag_configure("strong", background="#ffd6d6")
        tree.tag_configure("suspect", background="#fff0d0")
        tree._sort_col = None  # type: ignore[attr-defined]
        tree._sort_desc = False  # type: ignore[attr-defined]
        return tree

    def _fill_detector_box(self) -> None:
        labels = [m["label"] for m in self.cfg["detectors"]]
        self.detector_box["values"] = labels
        current = find_model(self.cfg["detectors"], self.cfg.get("active_detector", ""))
        if current and current["label"] in labels:
            self.detector_var.set(current["label"])
        elif labels:
            self.detector_var.set(labels[0])

    def _selected_detector(self) -> dict | None:
        label = self.detector_var.get()
        for model in self.cfg["detectors"]:
            if model["label"] == label:
                return model
        return find_model(self.cfg["detectors"], self.cfg.get("active_detector", ""))

    def _show_model_note(self) -> None:
        model = self._selected_detector()
        self.model_note.config(text=model.get("note", "") if model else "")

    def choose(self) -> None:
        path = filedialog.askopenfilename(
            title="Choisir un document",
            filetypes=[("Documents", "*.pdf *.docx"), ("PDF", "*.pdf"), ("Word", "*.docx")],
        )
        if not path:
            return
        self.pdf_path = path
        self.file_lbl.config(text=Path(path).name, foreground="#000")
        self.run_btn.config(state="normal")
        self._clear_results()
        self.status.config(text="Fichier chargé. Cliquez sur « Analyser ».")

    def _clear_results(self) -> None:
        self.findings = []
        self.result = None
        self.out_path = None
        self.summary.config(text="")
        self.tree.delete(*self.tree.get_children())
        self.reform_tree.delete(*self.reform_tree.get_children())
        for btn in (self.open_btn, self.save_btn, self.report_btn, self.reform_btn):
            btn.config(state="disabled")

    def run(self) -> None:
        if self.busy or not self.pdf_path:
            return
        if not self._selected_detector():
            messagebox.showwarning("Modèle", "Ajoutez un modèle de détection dans les réglages.")
            return
        self._start_worker(reformulate=bool(self.reform_var.get()), findings_only=False)

    def reformulate_only(self) -> None:
        if self.busy or not self.findings:
            return
        self._start_worker(reformulate=True, findings_only=True)

    def _start_worker(self, reformulate: bool, findings_only: bool) -> None:
        self.busy = True
        self.cancel.clear()
        self.run_btn.config(state="disabled")
        self.reform_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress.config(value=0)
        if not findings_only:
            self.tree.delete(*self.tree.get_children())
            self.reform_tree.delete(*self.reform_tree.get_children())
            self.summary.config(text="")
        options = dict(self.cfg)
        options["threshold"] = float(self.thr.get())
        spec = dict(self._selected_detector() or {})
        reform_spec = dict(find_model(self.cfg["reformulators"], self.cfg.get("active_reformulator", "")) or {})
        threading.Thread(
            target=self._worker,
            args=(self.pdf_path, options, spec, reform_spec, reformulate, findings_only, list(self.findings)),
            daemon=True,
        ).start()

    def stop(self) -> None:
        if self.busy:
            self.cancel.set()
            self.status.config(text="Arrêt demandé…")

    def _worker(self, path, options, spec, reform_spec, reformulate, findings_only, findings) -> None:
        try:
            self._apply_cache_dir(options.get("cache_dir") or "")
            if not findings_only:
                self._load_detector(spec, options)
                dest = output_path_for(path)

                def prog(done, total, msg):
                    self.q.put(("progress", 100 * done / max(total, 1), msg))

                result = analyse_document(path, self.detector, options, str(dest), prog, self.cancel)
            else:
                result = dict(self.result or {})
                result["findings"] = findings
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
                    self.q.put(("progress", 100 * (index - 1) / total, f"Reformulation {index}/{total}…"))
                    finding["reformulation"] = self.reformulator.paraphrase(finding["text"])
                if result.get("out_path"):
                    write_reformulations(result["out_path"], result["findings"])
            self.q.put(("done", result))
        except Exception as exc:  # noqa: BLE001
            self.q.put(("error", f"{type(exc).__name__}: {exc}", traceback.format_exc()))

    def _apply_cache_dir(self, cache_dir: str) -> None:
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            os.environ["HF_HOME"] = cache_dir

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
        self.q.put(("status", f"Chargement du modèle… (premier lancement : téléchargement de {spec.get('hf_id')})"))
        self.q.put(("indeterminate", True))
        self.detector = Detector(
            spec,
            device=options["device"],
            batch_size=int(options["batch_size"]),
            log=lambda msg: self.q.put(("status", msg)),
        )
        self.q.put(("calibration", self.detector.calibration))
        self.q.put(("indeterminate", False))

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
        self.q.put(("status", f"Chargement du reformulateur… (premier lancement : téléchargement de {spec.get('hf_id')})"))
        self.q.put(("indeterminate", True))
        self.reformulator = Reformulator(spec, device=options["device"], log=lambda msg: self.q.put(("status", msg)))
        self.q.put(("indeterminate", False))

    def _poll(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self.status.config(text=msg[1])
                elif kind == "calibration":
                    self.calib.config(text=msg[1])
                elif kind == "indeterminate":
                    if msg[1]:
                        self.progress.config(mode="indeterminate")
                        self.progress.start(12)
                    else:
                        self.progress.stop()
                        self.progress.config(mode="determinate", value=0)
                elif kind == "progress":
                    self.progress.config(mode="determinate", value=msg[1])
                    self.status.config(text=msg[2])
                elif kind == "done":
                    self._show(msg[1])
                elif kind == "error":
                    self._idle()
                    self.status.config(text="Erreur.")
                    _error_dialog(self, msg[1], msg[2] if len(msg) > 2 else msg[1])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _idle(self) -> None:
        self.busy = False
        self.progress.stop()
        self.progress.config(mode="determinate")
        self.stop_btn.config(state="disabled")
        self.run_btn.config(state="normal" if self.pdf_path else "disabled")

    def _show(self, result: dict) -> None:
        self._idle()
        self.result = result
        self.findings = result.get("findings") or []
        if result.get("calibration"):
            self.calib.config(text=result["calibration"])
        if result.get("cancelled"):
            self.status.config(text="Analyse interrompue.")
        if result.get("analysed_words", 0) == 0 and not result.get("cancelled"):
            self.summary.config(text="Aucun texte exploitable.")
            if (self.pdf_path or "").lower().endswith(".docx"):
                self.status.config(text="Le document Word ne contient pas assez de texte suivi.")
                detail = "Il faut des paragraphes assez longs. Les titres très courts sont ignorés."
            else:
                self.status.config(text="Le PDF ne contient pas assez de texte sélectionnable.")
                detail = (
                    "Si le PDF est un scan (images), faites d'abord une reconnaissance de caractères, "
                    "par exemple : ocrmypdf entree.pdf sortie.pdf."
                )
            messagebox.showwarning(
                "Pas de texte",
                "Aucun passage assez long n'a pu être analysé.\n\n" + detail,
            )
            return

        self.out_path = result.get("out_path")
        if self.out_path:
            self.open_btn.config(state="normal")
            self.save_btn.config(state="normal")
        if self.findings:
            self.report_btn.config(state="normal")
            self.reform_btn.config(state="normal")
        strong = float(self.cfg["strong_prob"])
        self.summary.config(
            text=(
                f"{result.get('verdict', '')} — {result.get('share', 0):.0%} des mots analysés signalés "
                f"({result.get('flagged_words', 0)}/{result.get('analysed_words', 0)}) · "
                f"probabilité moyenne {result.get('mean_prob', 0):.0%}"
            )
        )
        self.tree.delete(*self.tree.get_children())
        self.reform_tree.delete(*self.reform_tree.get_children())
        for index, finding in enumerate(self.findings):
            tag = "strong" if finding["prob"] >= strong else "suspect"
            excerpt = finding["text"][:180].replace("\n", " ")
            if len(finding["text"]) > 180:
                excerpt += "…"
            self.tree.insert("", "end", iid=f"f{index}", values=(finding["page"], f"{finding['prob']:.0%}", finding["words"], excerpt), tags=(tag,))
            reform = (finding.get("reformulation") or "").replace("\n", " ")
            if len(reform) > 220:
                reform = reform[:220] + "…"
            self.reform_tree.insert("", "end", iid=f"r{index}", values=(finding["page"], f"{finding['prob']:.0%}", reform or "—"), tags=(tag,))
        if self.out_path and not result.get("cancelled"):
            self.status.config(text=f"Copie surlignée enregistrée : {self.out_path}")
        if result.get("french_ratio", 1) < 0.08 and result.get("analysed_words", 0):
            messagebox.showwarning(
                "Langue",
                "Ce document ne semble pas être en français. CamemBERTa est conçu pour le français : "
                "les résultats ne seront pas fiables sur une autre langue. "
                "Vous pouvez essayer le modèle XLM-RoBERTa si le texte mélange français et anglais.",
            )

    def _sort_tree(self, tree: ttk.Treeview, column: str) -> None:
        descending = tree._sort_col == column and not tree._sort_desc  # type: ignore[attr-defined]
        rows = [(tree.set(item, column), item) for item in tree.get_children("")]

        def key(pair):
            value = pair[0].replace("%", "").replace("…", "")
            try:
                return float(value)
            except ValueError:
                return value.casefold()

        for index, (_, item) in enumerate(sorted(rows, key=key, reverse=descending)):
            tree.move(item, "", index)
        tree._sort_col = column  # type: ignore[attr-defined]
        tree._sort_desc = descending  # type: ignore[attr-defined]

    def _open_detail(self, tree: ttk.Treeview) -> None:
        selected = tree.selection()
        if not selected:
            return
        index = int(selected[0][1:])
        if index >= len(self.findings):
            return
        finding = self.findings[index]
        win = tk.Toplevel(self)
        win.title(f"Passage — page {finding['page']} — {finding['prob']:.0%}")
        win.geometry("760x520")
        ttk.Label(win, text="Passage signalé").pack(anchor="w", padx=12, pady=(10, 0))
        original = tk.Text(win, height=10, wrap="word")
        original.pack(fill="both", expand=True, padx=12, pady=4)
        original.insert("1.0", finding["text"])
        original.config(state="disabled")
        ttk.Label(win, text="Reformulation").pack(anchor="w", padx=12)
        rewritten = tk.Text(win, height=10, wrap="word")
        rewritten.pack(fill="both", expand=True, padx=12, pady=4)
        rewritten.insert("1.0", finding.get("reformulation") or "Pas encore de reformulation.")
        rewritten.config(state="disabled")

    def open_out(self) -> None:
        if not self.out_path:
            return
        _open_file(self.out_path)

    def save_as(self) -> None:
        if not self.out_path:
            return
        ext = Path(self.out_path).suffix.lower() or ".pdf"
        kind = ("Word", "*.docx") if ext == ".docx" else ("PDF", "*.pdf")
        dest = filedialog.asksaveasfilename(
            defaultextension=ext,
            filetypes=[kind],
            initialfile=Path(self.out_path).name,
        )
        if dest:
            shutil.copyfile(self.out_path, dest)
            self.status.config(text=f"Copie enregistrée : {dest}")

    def export_report(self) -> None:
        if not self.findings:
            return
        initial = "rapport_analyse_IA.txt"
        if self.pdf_path:
            initial = Path(self.pdf_path).stem + "_rapport_IA.txt"
        dest = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Texte", "*.txt")], initialfile=initial)
        if not dest:
            return
        Path(dest).write_text(self._report_text(), encoding="utf-8")
        self.status.config(text=f"Rapport enregistré : {dest}")

    def _report_text(self) -> str:
        result = self.result or {}
        lines = [
            "Détecteur de texte IA — rapport",
            f"Fichier : {self.pdf_path or ''}",
            f"Modèle : {(self._selected_detector() or {}).get('label', '')}",
            f"Seuil : {self.thr.get():.0%}",
            self.calib.cget("text"),
            result.get("verdict", ""),
            f"Mots signalés : {result.get('flagged_words', 0)}/{result.get('analysed_words', 0)} ({result.get('share', 0):.0%})",
            f"Copie surlignée : {self.out_path or ''}",
            "",
        ]
        place = "Paragraphe" if (self.pdf_path or "").lower().endswith(".docx") else "Page"
        for finding in self.findings:
            lines.append(f"--- {place} {finding['page']} · {finding['prob']:.0%} · {finding['words']} mots ---")
            lines.append(finding["text"])
            if finding.get("reformulation"):
                lines.append("")
                lines.append("Reformulation :")
                lines.append(finding["reformulation"])
            lines.append("")
        return "\n".join(lines)

    def open_settings(self) -> None:
        if self.busy:
            messagebox.showinfo("Réglages", "Attendez la fin de l'analyse avant de modifier les réglages.")
            return
        Settings(self, self.cfg).wait_window()
        self._fill_detector_box()
        self._show_model_note()
        save_config(self.cfg)

    def show_help(self) -> None:
        _text_dialog(self, "Mode d'emploi", HELP)

    def show_about(self) -> None:
        messagebox.showinfo(
            "À propos",
            f"Détecteur de texte IA {__version__}\n\n"
            "Détection : almanach/camemberta-chatgptdetect-noisy (Inria / ALMAnaCH)\n"
            "et almanach/xlmr-chatgptdetect-noisy.\n"
            "Reformulation : CATIE-AQ/french_paraphrase_flan-t5.\n\n"
            "Les réglages sont dans config.json, à côté de l'application.",
        )

    def _on_close(self) -> None:
        self.cfg["threshold"] = round(float(self.thr.get()), 2)
        self.cfg["reformulate_after_detection"] = bool(self.reform_var.get())
        selected = self._selected_detector()
        if selected:
            self.cfg["active_detector"] = selected["id"]
        try:
            save_config(self.cfg)
        except OSError:
            pass
        if self.detector is not None:
            self.detector.close()
        if self.reformulator is not None:
            self.reformulator.close()
        self.destroy()


class Settings(tk.Toplevel):
    def __init__(self, app: App, cfg: dict):
        super().__init__(app)
        self.app = app
        self.cfg = cfg
        self.title("Réglages")
        self.geometry("760x640")
        self.transient(app)
        self.grab_set()
        book = ttk.Notebook(self)
        book.pack(fill="both", expand=True, padx=12, pady=12)
        self._detection_tab(book)
        self._models_tab(book)
        self._reform_tab(book)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(buttons, text="Enregistrer", command=self._save).pack(side="right")
        ttk.Button(buttons, text="Annuler", command=self.destroy).pack(side="right", padx=8)

    def _detection_tab(self, book: ttk.Notebook) -> None:
        tab = ttk.Frame(book, padding=12)
        book.add(tab, text="Détection")
        self.strong = self._number(tab, "Seuil rouge (très probable)", self.cfg["strong_prob"], 0)
        self.min_words = self._number(tab, "Mots minimum d'un passage", self.cfg["min_words"], 1)
        self.target_words = self._number(tab, "Taille cible (mots)", self.cfg["target_words"], 2)
        self.max_words = self._number(tab, "Taille maximale (mots)", self.cfg["max_words"], 3)
        self.batch = self._number(tab, "Taille des lots", self.cfg["batch_size"], 4)
        self.opacity = self._number(tab, "Opacité du surlignage (0,15 à 1)", self.cfg["highlight_opacity"], 5)
        ttk.Label(tab, text="Processeur").grid(row=6, column=0, sticky="w", pady=6)
        self.device = tk.StringVar(value=self.cfg.get("device", "auto"))
        device = ttk.Combobox(tab, textvariable=self.device, state="readonly", values=("auto", "cpu", "cuda"), width=12)
        device.grid(row=6, column=1, sticky="w")
        ttk.Label(tab, text="Dossier de cache des modèles (vide = dossier Hugging Face par défaut)").grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 2))
        self.cache = tk.StringVar(value=self.cfg.get("cache_dir") or "")
        ttk.Entry(tab, textvariable=self.cache, width=68).grid(row=8, column=0, columnspan=2, sticky="we")
        ttk.Label(
            tab,
            text="« auto » utilise le GPU s'il existe, sinon le processeur. Le fichier config.json peut aussi être modifié à la main.",
            style="Muted.TLabel",
            wraplength=680,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=12)

    def _number(self, tab, label, value, row) -> tk.StringVar:
        ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=4)
        var = tk.StringVar(value=str(value))
        ttk.Entry(tab, textvariable=var, width=12).grid(row=row, column=1, sticky="w")
        return var

    def _models_tab(self, book: ttk.Notebook) -> None:
        tab = ttk.Frame(book, padding=12)
        book.add(tab, text="Modèles de détection")
        self.det_list = tk.Listbox(tab, height=8, exportselection=False)
        self.det_list.pack(fill="x")
        self.det_list.bind("<<ListboxSelect>>", lambda _e: self._load_detector_fields())
        form = ttk.Frame(tab)
        form.pack(fill="x", pady=8)
        self.det_label = self._field(form, "Nom affiché", 0)
        self.det_hf = self._field(form, "Identifiant Hugging Face", 1)
        self.det_force = self._field(form, "Indice de la classe IA (vide = automatique, 0 ou 1)", 2)
        self.det_note = self._field(form, "Description", 3)
        actions = ttk.Frame(tab)
        actions.pack(fill="x")
        ttk.Button(actions, text="Ajouter", command=self._add_detector).pack(side="left")
        ttk.Button(actions, text="Mettre à jour", command=self._update_detector).pack(side="left", padx=6)
        ttk.Button(actions, text="Retirer", command=self._remove_detector).pack(side="left")
        ttk.Label(
            tab,
            text="CamemBERTa reste le meilleur choix pour du français seul. XLM-RoBERTa sert surtout aux documents bilingues. "
                 "Un modèle ajouté ici doit être un classifieur de séquences Hugging Face.",
            style="Muted.TLabel",
            wraplength=680,
        ).pack(anchor="w", pady=8)
        self._refresh_det_list()

    def _reform_tab(self, book: ttk.Notebook) -> None:
        tab = ttk.Frame(book, padding=12)
        book.add(tab, text="Reformulation")
        ttk.Label(tab, text="Modèle utilisé après la détection").pack(anchor="w")
        self.ref_choice = tk.StringVar()
        labels = [m["label"] for m in self.cfg["reformulators"]]
        self.ref_box = ttk.Combobox(tab, textvariable=self.ref_choice, state="readonly", values=labels, width=52)
        self.ref_box.pack(anchor="w", pady=4)
        active = find_model(self.cfg["reformulators"], self.cfg.get("active_reformulator", ""))
        if active:
            self.ref_choice.set(active["label"])
        elif labels:
            self.ref_choice.set(labels[0])
        self.ref_box.bind("<<ComboboxSelected>>", lambda _e: self._load_reform_fields())
        self.ref_prompt = self._labeled_entry(tab, "Modèle de prompt ({text} sera remplacé par le passage)")
        self.ref_words = self._labeled_entry(tab, "Mots maximum envoyés au modèle d'un coup")
        self.ref_note = ttk.Label(tab, text="", style="Muted.TLabel", wraplength=680, justify="left")
        self.ref_note.pack(anchor="w", pady=8)
        add = ttk.LabelFrame(tab, text="Ajouter un autre modèle seq2seq", padding=8)
        add.pack(fill="x", pady=8)
        self.new_ref_label = self._field(add, "Nom affiché", 0)
        self.new_ref_hf = self._field(add, "Identifiant Hugging Face", 1)
        ttk.Button(add, text="Ajouter à la liste", command=self._add_reformulator).grid(row=2, column=0, sticky="w", pady=6)
        self._load_reform_fields()

    def _labeled_entry(self, parent, label) -> tk.StringVar:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(8, 0))
        var = tk.StringVar()
        ttk.Entry(parent, textvariable=var, width=78).pack(anchor="w", fill="x")
        return var

    def _field(self, parent, label, row) -> tk.StringVar:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar()
        ttk.Entry(parent, textvariable=var, width=52).grid(row=row, column=1, sticky="we", padx=6)
        return var

    def _refresh_det_list(self) -> None:
        self.det_list.delete(0, "end")
        active = self.cfg.get("active_detector")
        select_at = 0
        for index, model in enumerate(self.cfg["detectors"]):
            mark = " ●" if model["id"] == active else ""
            self.det_list.insert("end", model["label"] + mark)
            if model["id"] == active:
                select_at = index
        if self.cfg["detectors"]:
            self.det_list.selection_set(select_at)
            self.det_list.activate(select_at)
            self._load_detector_fields()

    def _selected_det_index(self) -> int | None:
        sel = self.det_list.curselection()
        return int(sel[0]) if sel else None

    def _load_detector_fields(self) -> None:
        index = self._selected_det_index()
        if index is None:
            return
        model = self.cfg["detectors"][index]
        self.det_label.set(model.get("label", ""))
        self.det_hf.set(model.get("hf_id", ""))
        forced = model.get("force_ai_label_id")
        self.det_force.set("" if forced is None else str(forced))
        self.det_note.set(model.get("note", ""))

    def _read_detector_fields(self) -> dict:
        hf_id = self.det_hf.get().strip()
        if "/" not in hf_id:
            raise ValueError("L'identifiant Hugging Face doit ressembler à organisation/nom-du-modele.")
        forced_raw = self.det_force.get().strip()
        forced = None if forced_raw == "" else int(forced_raw)
        if forced not in (None, 0, 1):
            raise ValueError("L'indice de classe doit être vide, 0 ou 1.")
        label = self.det_label.get().strip() or hf_id
        return {
            "id": _slug(hf_id),
            "hf_id": hf_id,
            "label": label,
            "note": self.det_note.get().strip(),
            "force_ai_label_id": forced,
        }

    def _add_detector(self) -> None:
        try:
            model = self._read_detector_fields()
        except ValueError as exc:
            messagebox.showerror("Modèle", str(exc), parent=self)
            return
        model["id"] = _unique_id(model["id"], self.cfg["detectors"])
        self.cfg["detectors"].append(model)
        self.cfg["active_detector"] = model["id"]
        self._refresh_det_list()

    def _update_detector(self) -> None:
        index = self._selected_det_index()
        if index is None:
            return
        try:
            updated = self._read_detector_fields()
        except ValueError as exc:
            messagebox.showerror("Modèle", str(exc), parent=self)
            return
        updated["id"] = self.cfg["detectors"][index]["id"]
        self.cfg["detectors"][index] = updated
        self._refresh_det_list()

    def _remove_detector(self) -> None:
        index = self._selected_det_index()
        if index is None or len(self.cfg["detectors"]) <= 1:
            messagebox.showinfo("Modèle", "Gardez au moins un modèle de détection.", parent=self)
            return
        del self.cfg["detectors"][index]
        self._refresh_det_list()

    def _current_reformulator(self) -> dict | None:
        label = self.ref_choice.get()
        for model in self.cfg["reformulators"]:
            if model["label"] == label:
                return model
        return None

    def _load_reform_fields(self) -> None:
        model = self._current_reformulator()
        if not model:
            return
        self.ref_prompt.set(model.get("prompt") or "{text}")
        self.ref_words.set(str(model.get("max_words_per_call") or 60))
        self.ref_note.config(text=model.get("note", ""))

    def _add_reformulator(self) -> None:
        hf_id = self.new_ref_hf.get().strip()
        label = self.new_ref_label.get().strip() or hf_id
        if "/" not in hf_id:
            messagebox.showerror("Modèle", "L'identifiant Hugging Face doit ressembler à organisation/nom-du-modele.", parent=self)
            return
        model = {
            "id": _slug(hf_id),
            "hf_id": hf_id,
            "label": label,
            "note": "Modèle ajouté manuellement.",
            "prompt": "{text}",
            "max_words_per_call": 60,
            "max_new_tokens": 220,
            "num_beams": 4,
            "repetition_penalty": 1.15,
            "no_repeat_ngram_size": 3,
            "length_penalty": 1.05,
        }
        model["id"] = _unique_id(model["id"], self.cfg["reformulators"])
        self.cfg["reformulators"].append(model)
        self.cfg["active_reformulator"] = model["id"]
        self.ref_box["values"] = [m["label"] for m in self.cfg["reformulators"]]
        self.ref_choice.set(label)
        self._load_reform_fields()

    def _save(self) -> None:
        try:
            self.cfg["strong_prob"] = float(self.strong.get().replace(",", "."))
            self.cfg["min_words"] = int(float(self.min_words.get()))
            self.cfg["target_words"] = int(float(self.target_words.get()))
            self.cfg["max_words"] = int(float(self.max_words.get()))
            self.cfg["batch_size"] = int(float(self.batch.get()))
            self.cfg["highlight_opacity"] = float(self.opacity.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Réglages", "Un des nombres n'est pas valide.", parent=self)
            return
        self.cfg["device"] = self.device.get()
        self.cfg["cache_dir"] = self.cache.get().strip()
        model = self._current_reformulator()
        if model:
            try:
                model["max_words_per_call"] = max(15, int(float(self.ref_words.get())))
            except ValueError:
                messagebox.showerror("Réglages", "Le nombre de mots du reformulateur n'est pas valide.", parent=self)
                return
            prompt = self.ref_prompt.get().strip() or "{text}"
            if "{text}" not in prompt:
                prompt = prompt + " {text}"
            model["prompt"] = prompt
            self.cfg["active_reformulator"] = model["id"]
        index = self._selected_det_index()
        if index is not None:
            self.cfg["active_detector"] = self.cfg["detectors"][index]["id"]
        from detectoria.config import _clamp
        _clamp(self.cfg)
        save_config(self.cfg)
        self.destroy()


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


def _open_file(path: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _text_dialog(parent, title: str, content: str) -> None:
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry("680x520")
    text = tk.Text(win, wrap="word", padx=8, pady=8)
    text.pack(fill="both", expand=True)
    text.insert("1.0", content)
    text.config(state="disabled")


def _error_dialog(parent, summary: str, details: str) -> None:
    win = tk.Toplevel(parent)
    win.title("Erreur")
    win.geometry("720x420")
    ttk.Label(win, text=summary, wraplength=680).pack(anchor="w", padx=12, pady=8)
    text = tk.Text(win, wrap="word")
    text.pack(fill="both", expand=True, padx=12, pady=4)
    text.insert("1.0", details)
    text.config(state="disabled")
    ttk.Button(win, text="Fermer", command=win.destroy).pack(pady=8)
