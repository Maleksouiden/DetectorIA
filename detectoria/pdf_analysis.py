"""Extraction du texte d'un PDF, découpage et surlignage."""

from __future__ import annotations

import re
import threading
from pathlib import Path

try:
    import pymupdf
except ImportError:  # PyMuPDF < 1.24
    import fitz as pymupdf

FR_STOPWORDS = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "en", "est", "que",
    "qui", "dans", "pour", "pas", "sur", "au", "aux", "avec", "ce", "cette",
    "il", "elle", "nous", "vous", "ils", "sont", "ont", "par", "plus", "ne",
    "se", "son", "sa", "ses", "à", "ou", "mais", "comme", "être", "fait",
}


def page_words(page, pymupdf_mod=pymupdf) -> list[dict]:
    """Mots d'une page, sans titres courts, en-têtes, pieds de page, sommaire ni tableaux."""
    raw = page.get_text("words")
    blocks: dict = {}
    for x0, y0, x1, y1, txt, block, line, word in raw:
        blocks.setdefault(block, []).append((line, word, pymupdf_mod.Rect(x0, y0, x1, y1), txt))

    height = page.rect.height
    kept = []
    for block in sorted(blocks):
        items = sorted(blocks[block], key=lambda t: (t[0], t[1]))
        text = " ".join(t[3] for t in items)
        count = len(items)
        top = min(t[2].y0 for t in items)
        bottom = max(t[2].y1 for t in items)
        letters = sum(c.isalpha() for c in text)
        if count < 5:
            continue
        if (bottom < height * 0.07 or top > height * 0.93) and count < 15:
            continue
        if letters < 0.6 * max(len(text), 1):
            continue
        if re.search(r"\.{4,}|(\. ){4,}", text):
            continue
        for line, _, rect, txt in items:
            kept.append({"text": txt, "rect": rect, "line": (block, line)})
    return kept


def make_chunks(words: list[dict], min_words: int, target_words: int, max_words: int) -> list[tuple[int, int]]:
    """Découpe en passages ; coupe de préférence en fin de paragraphe, sinon en fin de phrase."""
    chunks: list[tuple[int, int]] = []
    start, n = 0, len(words)
    ends = (".", "!", "?", "…", ":")

    def para_end(i: int) -> bool:
        return i >= n or words[i - 1]["line"][0] != words[i]["line"][0]

    def sent_end(i: int) -> bool:
        return words[i - 1]["text"].rstrip('"»)”’').endswith(ends)

    while start < n:
        if n - start <= max_words:
            chunks.append((start, n))
            break
        lo, hi = start + min_words, min(start + max_words, n)
        target = start + target_words
        end = None
        for test in (para_end, sent_end):
            cands = [i for i in range(lo, hi + 1) if test(i)]
            if cands:
                end = min(cands, key=lambda i: abs(i - target))
                break
        end = end or min(start + target_words, n)
        chunks.append((start, end))
        start = end
    if len(chunks) > 1 and chunks[-1][1] - chunks[-1][0] < min_words:
        start_, end_ = chunks.pop()
        prev_start, _ = chunks.pop()
        chunks.append((prev_start, end_))
    return [c for c in chunks if c[1] - c[0] >= min_words]


def line_rects(words: list[dict]):
    lines = {}
    for word in words:
        current = lines.get(word["line"])
        lines[word["line"]] = pymupdf.Rect(word["rect"]) if current is None else (current | word["rect"])
    return list(lines.values())


def french_ratio(all_words: list[str]) -> float:
    if not all_words:
        return 0.0
    hits = sum(1 for word in all_words if word.lower().strip(".,;:!?()«»\"'") in FR_STOPWORDS)
    return hits / len(all_words)


def verdict(share: float) -> str:
    if share < 0.10:
        return "Peu ou pas de passages signalés"
    if share < 0.35:
        return "Quelques passages signalés"
    if share < 0.65:
        return "Une grande partie du texte est signalée"
    return "La majorité du texte est signalée"


def output_path_for(source_path: str) -> Path:
    source = Path(source_path)
    ext = source.suffix if source.suffix else ".pdf"
    name = source.stem + "_analyse_IA" + ext
    dest = source.with_name(name)
    try:
        dest.parent.mkdir(exist_ok=True)
        with dest.open("ab"):
            pass
        return dest
    except OSError:
        return Path.home() / name


def analyse_pdf(pdf_path, detector, options: dict, out_path, progress=lambda *a: None, cancel: threading.Event | None = None):
    doc = pymupdf.open(pdf_path)
    n_pages = len(doc)
    findings = []
    passages = []
    analysed_words = flagged_words = 0
    weighted = 0.0
    sample_words: list[str] = []
    threshold = float(options["threshold"])
    strong = float(options["strong_prob"])
    color_suspect = tuple(options["color_suspect"])
    color_strong = tuple(options["color_strong"])
    opacity = float(options["highlight_opacity"])
    stopped = False

    try:
        for pno in range(n_pages):
            if cancel is not None and cancel.is_set():
                stopped = True
                break
            page = doc[pno]
            progress(pno, n_pages, f"Analyse de la page {pno + 1}/{n_pages}…")
            words = page_words(page)
            if len(sample_words) < 4000:
                sample_words.extend(w["text"] for w in words[:400])
            spans = make_chunks(
                words,
                int(options["min_words"]),
                int(options["target_words"]),
                int(options["max_words"]),
            )
            if not spans:
                continue
            texts = [" ".join(w["text"] for w in words[s:e]) for s, e in spans]
            probs = detector.predict(texts)

            for (start, end), text, prob in zip(spans, texts, probs):
                count = end - start
                analysed_words += count
                weighted += prob * count
                passage = {
                    "page": pno + 1,
                    "prob": prob,
                    "words": count,
                    "text": text,
                    "reformulation": "",
                    "flagged": prob >= threshold,
                }
                passages.append(passage)
                if prob >= threshold:
                    flagged_words += count
                    annot = page.add_highlight_annot(quads=line_rects(words[start:end]))
                    annot.set_colors(stroke=color_strong if prob >= strong else color_suspect)
                    annot.set_opacity(opacity)
                    annot.set_info(
                        title="Détecteur IA",
                        content=f"Probabilité de texte généré par IA : {prob:.0%}",
                    )
                    annot.update()
                    findings.append(passage)

        if analysed_words and not stopped:
            doc.save(out_path, garbage=3, deflate=True)
    finally:
        doc.close()

    progress(n_pages, n_pages, "Terminé" if not stopped else "Analyse interrompue")
    share = flagged_words / analysed_words if analysed_words else 0.0
    return {
        "pages": n_pages,
        "analysed_words": analysed_words,
        "flagged_words": flagged_words,
        "share": share,
        "mean_prob": weighted / analysed_words if analysed_words else 0.0,
        "verdict": verdict(share) if analysed_words else "Aucun texte exploitable",
        "findings": findings,
        "passages": passages,
        "french_ratio": french_ratio(sample_words),
        "out_path": str(out_path) if analysed_words and not stopped else None,
        "cancelled": stopped,
        "model_label": getattr(detector, "spec", {}).get("label", ""),
        "calibration": getattr(detector, "calibration", ""),
        "kind": "pdf",
        "place_label": "Page",
    }


def analyse_document(path, detector, options: dict, out_path, progress=lambda *a: None, cancel: threading.Event | None = None):
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        from detectoria.docx_analysis import analyse_docx

        return analyse_docx(path, detector, options, out_path, progress, cancel)
    if suffix == ".doc":
        raise RuntimeError(
            "Les anciens fichiers .doc ne sont pas lus. "
            "Enregistrez le document au format .docx, puis relancez l'analyse."
        )
    return analyse_pdf(path, detector, options, out_path, progress, cancel)


def write_reformulations(pdf_path: str, findings: list[dict]) -> None:
    """Ajoute la reformulation dans la copie analysée, sans toucher au fichier source."""
    if not pdf_path or not findings:
        return
    if str(pdf_path).lower().endswith(".docx"):
        from detectoria.docx_analysis import write_docx_reformulations

        write_docx_reformulations(pdf_path, findings)
        return
    doc = pymupdf.open(pdf_path)
    try:
        by_page: dict[int, list[dict]] = {}
        for finding in findings:
            by_page.setdefault(finding["page"], []).append(finding)
        for page_no, items in by_page.items():
            page = doc[page_no - 1]
            highlights = []
            annots = page.annots()
            if annots:
                for annot in annots:
                    info = annot.info or {}
                    if info.get("title") == "Détecteur IA":
                        highlights.append(annot)
            for annot, item in zip(highlights, items):
                reform = (item.get("reformulation") or "").strip()
                content = f"Probabilité de texte généré par IA : {item['prob']:.0%}"
                if reform:
                    content += f"\n\nReformulation proposée :\n{reform}"
                annot.set_info(title="Détecteur IA", content=content)
                annot.update()
        try:
            doc.saveIncr()
        except Exception:
            temp = Path(pdf_path).with_suffix(".tmp.pdf")
            doc.save(temp, garbage=3, deflate=True)
            doc.close()
            doc = None
            temp.replace(pdf_path)
    finally:
        if doc is not None:
            doc.close()
