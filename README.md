# Printy

Add-on Home Assistant pour gérer une production d'impressions 3D : bibliothèque de
fichiers tranchés, imprimantes, planning de la semaine, allumage et extinction par
prise connectée, confirmation « plateau vide » par notification avec photo.

- Cahier des charges et feuille de route : [`docs/SPEC.md`](docs/SPEC.md)
- Documentation utilisateur de l'add-on : [`printy/DOCS.md`](printy/DOCS.md)

## Structure

```
repository.yaml          dépôt d'add-ons Home Assistant
printy/                  l'add-on
  config.yaml, Dockerfile, run.sh, build.yaml
  backend/               Python 3.12+, FastAPI, SQLAlchemy, SQLite
    printy/
      drivers/           un pilote par marque (creality, moonraker, demo)
      api/               routes REST de l'interface + API compatible OctoPrint
      scheduler.py       cycle de vie des impressions planifiées
      ha.py              Home Assistant (prises, caméras, notifications)
      library.py, gcode.py
    tests/
  frontend/              Vue 3, Vite, TypeScript
```

## Développer en local

Sans Home Assistant, Printy tourne en mode développement : données dans `.dev/`,
prises simulées, notifications seulement journalisées.

```bash
# backend
python -m venv .venv
.venv/Scripts/pip install -r printy/backend/requirements.txt pytest   # Windows
cd printy/backend
PRINTY_DEV_DIR=../../.dev ../../.venv/Scripts/python -m uvicorn printy.main:app --port 8099 --reload

# données de démonstration (serveur lancé)
../../.venv/Scripts/python scripts/seed_demo.py

# interface (autre terminal), proxy /api vers :8099
cd printy/frontend
npm install
npm run dev
```

Tests : `cd printy/backend && ../../.venv/Scripts/python -m pytest`.

`npm run build` compile l'interface dans `printy/backend/printy/static`, servie par
FastAPI.

## Installer dans Home Assistant

1. Pousse ce dépôt sur GitHub.
2. Home Assistant → Paramètres → Modules complémentaires → Boutique → ⋮ → Dépôts :
   ajoute l'URL du dépôt (mets à jour `url` dans `repository.yaml`).
3. Installe **Printy**, démarre-le, puis active « Afficher dans la barre latérale ».

Pour itérer sans GitHub : copie le dossier `printy/` dans `/addons/printy` sur
Home Assistant (add-on Samba), puis Boutique → ⋮ → Rechercher des mises à jour : il
apparaît dans « Modules complémentaires locaux ».
