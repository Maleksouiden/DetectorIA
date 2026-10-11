"""Reformulation des passages signalés, phrase par phrase."""

from __future__ import annotations

import gc
import re

from detectoria.detector import resolve_device


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def group_sentences(text: str, max_words: int) -> list[str]:
    groups: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in split_sentences(text):
        words = len(sentence.split())
        if current and count + words > max_words:
            groups.append(" ".join(current))
            current, count = [], 0
        current.append(sentence)
        count += words
    if current:
        groups.append(" ".join(current))
    return groups or ([text.strip()] if text.strip() else [])


class Reformulator:
    def __init__(self, spec: dict, device: str = "auto", log=print):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.torch = torch
        self.spec = spec
        self.device = resolve_device(device)
        hf_id = spec["hf_id"]
        log(f"Chargement du reformulateur {spec.get('label', hf_id)} ({self.device})…")
        self.tok = AutoTokenizer.from_pretrained(hf_id)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(hf_id)
        self.model.to(self.device).eval()
        log("Reformulateur prêt.")

    def close(self) -> None:
        model = getattr(self, "model", None)
        self.model = None
        self.tok = None
        del model
        gc.collect()
        if self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()

    def paraphrase(self, text: str) -> str:
        prompt_tpl = self.spec.get("prompt") or "{text}"
        max_words = int(self.spec.get("max_words_per_call") or 60)
        pieces = []
        for chunk in group_sentences(text, max_words):
            prompt = prompt_tpl.replace("{text}", chunk)
            pieces.append(self._generate(prompt, chunk))
        return " ".join(piece for piece in pieces if piece).strip()

    def _generate(self, prompt: str, source: str) -> str:
        torch = self.torch
        max_new = int(self.spec.get("max_new_tokens") or 220)
        word_count = max(8, len(source.split()))
        max_new = min(max_new, max(48, int(word_count * 2.2)))
        enc = self.tok(prompt, return_tensors="pt", truncation=True, max_length=480).to(self.device)
        with torch.no_grad():
            out = self.model.generate(
                **enc,
                max_new_tokens=max_new,
                num_beams=int(self.spec.get("num_beams") or 4),
                repetition_penalty=float(self.spec.get("repetition_penalty") or 1.15),
                no_repeat_ngram_size=int(self.spec.get("no_repeat_ngram_size") or 3),
                length_penalty=float(self.spec.get("length_penalty") or 1.05),
                early_stopping=True,
            )
        decoded = self.tok.decode(out[0], skip_special_tokens=True).strip()
        decoded = re.sub(r"^(paraphrase\s*:|reformulation\s*:)\s*", "", decoded, flags=re.I)
        return decoded or source
