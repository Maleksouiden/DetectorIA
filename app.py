#!/usr/bin/env python3
"""Lance l'application Détecteur IA."""

from detectoria.bootstrap import ensure_dependencies

ensure_dependencies()

from detectoria.desktop import main  # noqa: E402

if __name__ == "__main__":
    main()
