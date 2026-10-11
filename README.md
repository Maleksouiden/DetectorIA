# Détecteur IA

Application Windows qui estime si un texte en français a été généré par une IA. Elle analyse un PDF ou un document Word, puis crée une copie surlignée. Le fichier d'origine n'est jamais modifié.

Les résultats sont indicatifs. Ils ne constituent pas une preuve.

## Installation

1. Téléchargez le dépôt.
2. Lancez `setup.exe`.
3. Ouvrez **Détecteur IA** depuis le bureau ou le menu Démarrer.

L'installateur place le programme dans votre dossier personnel. Les droits administrateur ne sont pas nécessaires. PyTorch est téléchargé pendant l'installation. Le modèle d'analyse est téléchargé au premier lancement de l'analyse.

## Utilisation

1. Choisissez un PDF ou un document Word (`.docx`).
2. Réglez le seuil. À 80 %, l'application signale moins de passages qu'avec un seuil plus bas.
3. Cliquez sur **Analyser**.

Le jaune correspond à un passage au-dessus du seuil. Le rouge correspond au seuil « très probable » (95 % par défaut). La copie est enregistrée à côté du document, sous le nom `nom_analyse_IA.pdf` ou `nom_analyse_IA.docx`.

La case de reformulation propose une autre façon d'écrire les passages signalés. Ce n'est ni une preuve, ni un moyen de masquer l'origine d'un texte.

Les anciens fichiers `.doc` ne sont pas lus. Enregistrez-les d'abord au format `.docx`. Un PDF composé uniquement d'images doit passer par une reconnaissance de caractères avant l'analyse.

## Lancement depuis les sources

Python 3.10 ou plus récent est requis.

```bat
lancer.bat
```

Le premier lancement installe les bibliothèques listées dans `requirements.txt`, puis PyTorch. Sur une machine sans carte NVIDIA, la version processeur est utilisée.

## Modèles

| Rôle | Modèle par défaut | Usage |
| --- | --- | --- |
| Détection | CamemBERTa (Inria) | Français |
| Détection | XLM-RoBERTa (Inria) | Français et anglais |
| Reformulation | Flan-T5 (CATIE) | Paraphrase en français |

D'autres modèles Hugging Face peuvent être ajoutés dans **Réglages**.

## Licence

Le logiciel est open source sous la licence MIT (`LICENSE`). Toute personne peut l'utiliser, l'étudier, le modifier et le partager.

Le fichier `DROITS.md` précise les conditions d'usage. Les auteurs ne sont pas responsables des impacts matériels ou autres. L'installation et l'utilisation se font sous la seule responsabilité de l'utilisateur.
