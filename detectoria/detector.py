"""Classifieurs Hugging Face, avec calibrage de la classe « IA »."""

from __future__ import annotations

import gc
import re

# Trois textes typiques de ChatGPT et trois textes oraux, pour vérifier
# que la classe « IA » n'est pas inversée.
CALIB_AI = [
    "Il est important de noter que la gestion de projet joue un rôle crucial dans la réussite "
    "d'une organisation. En effet, une planification rigoureuse permet de définir clairement les "
    "objectifs, d'allouer efficacement les ressources et de minimiser les risques. De plus, la "
    "communication entre les parties prenantes favorise la collaboration et garantit l'alignement "
    "de tous les acteurs. En conclusion, adopter une approche structurée est essentiel pour atteindre "
    "durablement les résultats escomptés.",
    "Voici plusieurs conseils pour améliorer votre productivité au quotidien. Tout d'abord, il est "
    "essentiel de définir des priorités claires afin de se concentrer sur les tâches les plus "
    "importantes. Ensuite, vous pouvez utiliser des outils de planification pour organiser votre "
    "temps de manière efficace. Enfin, n'oubliez pas de prendre des pauses régulières, car elles "
    "contribuent à maintenir un bon niveau de concentration et à réduire le stress.",
    "L'intelligence artificielle est un domaine en constante évolution qui offre de nombreuses "
    "opportunités dans des secteurs variés tels que la santé, l'éducation et l'industrie. Cependant, "
    "elle soulève également des questions éthiques importantes, notamment en matière de protection "
    "des données personnelles et de transparence des algorithmes. Il convient donc de trouver un "
    "équilibre entre innovation et responsabilité.",
]
CALIB_HUMAN = [
    "Franchement j'sais pas trop ce qui s'est passé hier soir, j'suis rentré vers minuit et ma "
    "voisine faisait déjà un boucan pas possible. J'ai tapé au mur deux trois fois, rien à faire. "
    "Du coup j'ai mis mes écouteurs et j'ai fini par m'endormir devant un vieux film que j'avais "
    "déjà vu dix fois. Bref, ce matin j'étais complètement mort au boulot.",
    "Bon alors moi ce que j'ai fait c'est que j'ai tout démonté, la carte mère comprise, parce que "
    "le ventilo faisait un bruit d'enfer. En fait c'était juste un câble qui frottait, la honte. "
    "Mais bon au moins j'en ai profité pour changer la pâte thermique, ça chauffe beaucoup moins "
    "maintenant, genre dix degrés de moins en jeu.",
    "On est partis samedi matin avec les gamins, il pleuvait des cordes mais ils voulaient "
    "absolument voir la mer. Résultat : trempés jusqu'aux os, un parapluie cassé et une glace "
    "mangée sous un abri de bus. Ils étaient ravis quand même. Moi un peu moins, surtout en "
    "voyant l'état de la voiture au retour.",
]


def resolve_device(choice: str) -> str:
    import torch

    if choice == "cpu":
        return "cpu"
    if choice == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA est demandé dans les réglages, mais aucun GPU n'est disponible.")
        return "cuda"
    return "cuda" if torch.cuda.is_available() else "cpu"


class Detector:
    def __init__(self, spec: dict, device: str = "auto", batch_size: int = 8, log=print):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.torch = torch
        self.spec = spec
        self.batch_size = batch_size
        self.device = resolve_device(device)
        hf_id = spec["hf_id"]
        log(f"Chargement de {spec.get('label', hf_id)} ({self.device})…")
        self.tok = AutoTokenizer.from_pretrained(hf_id)
        self.model = AutoModelForSequenceClassification.from_pretrained(hf_id)
        self.model.to(self.device).eval()
        self.ai_id = self._resolve_ai_label()
        self.calibration = self._calibrate()
        log(self.calibration)

    def close(self) -> None:
        model = getattr(self, "model", None)
        self.model = None
        self.tok = None
        del model
        gc.collect()
        torch = getattr(self, "torch", None)
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _resolve_ai_label(self) -> int:
        forced = self.spec.get("force_ai_label_id")
        if forced is not None:
            return int(forced)
        labels = {int(i): str(n).lower() for i, n in self.model.config.id2label.items()}
        for i, name in labels.items():
            if re.search(r"(chatgpt|gpt|\bai\b|\bia\b|machine|generated|fake|bot|llm)", name):
                return i
        for i, name in labels.items():
            if re.search(r"(human|humain|real)", name) and len(labels) == 2:
                return [j for j in labels if j != i][0]
        return 1

    def _calibrate(self) -> str:
        p_ai = sum(self.predict(CALIB_AI)) / len(CALIB_AI)
        p_hu = sum(self.predict(CALIB_HUMAN)) / len(CALIB_HUMAN)
        if p_ai < p_hu - 0.3 and self.spec.get("force_ai_label_id") is None:
            n_labels = int(getattr(self.model.config, "num_labels", 2) or 2)
            if n_labels == 2:
                self.ai_id = 1 - self.ai_id
                p_ai, p_hu = 1 - p_ai, 1 - p_hu
                return (
                    f"Calibrage : classes inversées automatiquement "
                    f"(textes IA {p_ai:.0%} / textes humains {p_hu:.0%})."
                )
            return (
                f"Calibrage incohérent (textes IA {p_ai:.0%} / textes humains {p_hu:.0%}). "
                "Indiquez l'indice de la classe IA dans les réglages."
            )
        return f"Calibrage : textes IA de test {p_ai:.0%} / textes humains de test {p_hu:.0%}."

    def predict(self, texts: list[str], batch_size: int | None = None) -> list[float]:
        torch = self.torch
        size = batch_size or self.batch_size
        out: list[float] = []
        for i in range(0, len(texts), size):
            batch = texts[i : i + size]
            enc = self.tok(
                batch, return_tensors="pt", padding=True, truncation=True, max_length=512
            ).to(self.device)
            with torch.no_grad():
                logits = self.model(**enc).logits
            if logits.shape[-1] == 1:
                probs = torch.sigmoid(logits).squeeze(-1)
            else:
                probs = torch.softmax(logits, dim=-1)[:, self.ai_id]
            if probs.ndim == 0:
                out.append(float(probs.cpu()))
            else:
                out.extend(probs.cpu().tolist())
        return out
