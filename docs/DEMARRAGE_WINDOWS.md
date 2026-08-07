# 🪟 Démarrage en local sur Windows

> Guide officiel pour Windows. Le script `setup.sh` fonctionne sur Windows
> via Git Bash ou WSL2 — ce guide couvre les deux options, plus PowerShell.

---

## 🧭 Choix de l'environnement

| Option | Avantages | Inconvénients | Recommandé pour |
|---|---|---|---|
| **WSL2 (Ubuntu 24.04)** | Identique à la prod, filesystem unifié, perf native | Setup plus long, ~5 Go | Production locale, long terme |
| **Git Bash + Python natif Windows** | Léger, démarre vite, intégration IDE | Perf filesystem réduite | Découverte, dev frontend |
| **PowerShell pur** | Natif Windows, pas de couche | Pas de `setup.sh` (PowerShell natif), à scripter à part | Environnements restreints |

Recommandation : **WSL2 pour le backend Python** (Meilleure compatibilité
LightGBM, Polars, CCXT), **Git Bash pour le frontend Next.js** si vous
préférez éviter WSL.

---

## 📋 Prérequis communs

### 1. Installer Git pour Windows

Téléchargez et installez Git Bash depuis https://git-scm.com/download/win.
Cela fournit `git`, `bash`, `openssl`, et les outils Unix de base.

### 2. Installer Python 3.14

1. Allez sur https://www.python.org/downloads/windows/
2. Téléchargez **Python 3.14.x** (Windows installer 64-bit).
3. Lancez l'installeur. ⚠ **Cochez impérativement** :
   - ☑ **Add python.exe to PATH**
   - ☑ **Install for all users** (recommandé)
4. Vérifiez dans Git Bash :
   ```bash
   py -3.14 --version
   # ou
   python --version  # si PATH configuré
   ```

### 3. Installer Node.js 20+

1. Allez sur https://nodejs.org/en/download/
2. Téléchargez le **LTS 20.x** pour Windows.
3. Lancez l'installeur (cochez "Add to PATH" par défaut).
4. Vérifiez :
   ```bash
   node --version  # doit afficher v20.x ou plus
   npm --version
   ```

---

## 🐧 Option A — WSL2 (recommandé pour le backend)

### A.1 Installer WSL2 + Ubuntu 24.04

```powershell
# Dans PowerShell en administrateur
wsl --install -d Ubuntu-24.04
# Redémarrez Windows, ouvrez Ubuntu, créez votre user Linux
```

### A.2 Installer Python 3.14 dans WSL

```bash
# Dans Ubuntu WSL
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update && sudo apt install -y \
  python3.14 python3.14-venv python3.14-dev \
  build-essential
python3.14 --version
```

### A.3 Cloner le repo

```bash
# Depuis Windows (le repo sera accessible dans /mnt/c/...)
cd /mnt/c/Users/$USER/Documents
git clone https://github.com/montreuild/bot-crypto.git
cd bot-crypto
```

### A.4 Lancer le setup

```bash
bash scripts/setup.sh
```

Le script détecte automatiquement Linux et installe tout.

---

## 🪟 Option B — Git Bash (recommandé pour le frontend)

### B.1 Ouvrir Git Bash

Touche Windows → taper "Git Bash" → Entrée.

### B.2 Cloner le repo

```bash
cd /c/Users/$USER/Documents
git clone https://github.com/montreuild/bot-crypto.git
cd bot-crypto
```

### B.3 Lancer le setup

```bash
bash scripts/setup.sh
```

Le script détecte automatiquement Windows via Git Bash et :
1. Cherche Python 3.14 via `py -3.14` (launcher officiel Windows).
2. Crée le venv dans `.venv/` (avec `Scripts/activate` au lieu de `bin/activate`).
3. Active automatiquement le bon chemin selon l'OS.
4. Génère `.env` avec une `WEB_API_KEY` aléatoire.

---

## ⚡ Option C — PowerShell (sans setup.sh)

Pour les environnements sans Git Bash (rare) :

```powershell
# Clone
git clone https://github.com/montreuild/bot-crypto.git
cd bot-crypto

# Venv
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1

# Si erreur "scripts désactivés", autorisez temporairement :
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Dépendances (runtime)
pip install -r requirements.txt
# Outillage de développement — nécessaire pour lancer pytest, ruff ou mypy :
pip install -r requirements-dev.txt

# Générer WEB_API_KEY
python -c "import secrets; print(f'WEB_API_KEY={secrets.token_hex(32)}')" > .env
# Éditez .env pour ajouter vos clés OKX si live

# Frontend
cd frontend
npm install
cd ..

# Démarrer en paper mode
python cli.py --paper
```

---

## 🚀 Démarrer le bot (toutes options)

### Paper mode (sans clés API)

```bash
# Backend + UI sur http://127.0.0.1:8000
python cli.py --paper
```

### Avec frontend Next.js

Dans un second terminal :

```bash
cd frontend
npm run dev   # http://localhost:3000
```

Le backend FastAPI reste sur `:8000`, le frontend Next.js sur `:3000`.
Le frontend est configuré pour proxifier `/api/*` vers `:8000`
(voir `frontend/src/lib/api.ts`).

### Live mode (⚠ risque réel)

1. Éditez `.env` :
   ```
   OKX_API_KEY=votre_cle_api
   OKX_API_SECRET=votre_secret
   OKX_API_PASSWORD=votre_passphrase
   ```
2. Éditez `config.yaml` :
   ```yaml
   trading:
     paper_mode: false    # ← passe en live réel
   ```
3. ⚠ Lisez d'abord `PRODUCTION_READINESS.md` (checklist Go/No-Go).

---

## 🐛 Dépannage Windows

### `py -3.14: command not found`

Le launcher Python n'est pas dans le PATH. Solutions :
- Relancez l'installeur Python → "Modify" → cochez "Add to PATH".
- Ou utilisez le chemin complet : `C:\Users\You\AppData\Local\Programs\Python\Python314\python.exe`.

### `pip` lent ou timeout

Configurez un miroir PyPI plus proche :
```bash
pip config set global.index-url https://pypi.org/simple/
# ou miroir chinois si plus proche :
# pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### LightGBM build erreur

Sur Windows natif (sans WSL), LightGBM peut nécessiter Visual C++
Build Tools. Téléchargez-les depuis
https://visualstudio.microsoft.com/visual-cpp-build-tools/
et installez "Desktop development with C++".

Alternative : utilisez WSL2 (Option A) où LightGBM s'installe sans build.

### ⚠ NumPy / Polars / LightGBM : "Unknown compiler(s)" ou compilation source

Si vous voyez ce type d'erreur pendant `pip install -r requirements.txt` :

```
Preparing metadata (pyproject.toml) did not run successfully
..meson.build:1:0: ERROR: Unknown compiler(s): [['icl'], ['cl'], ['cc'], ['gcc'], ['clang'], ['clang-cl'], ['pgcc']]
```

C'est que la version épinglée dans `requirements.txt` n'a **pas de wheel**
pour Python 3.14 sur Windows. pip tente donc de compiler depuis le source
et échoue car il n'y a pas de compilateur C (Visual C++ Build Tools).

**Solution** : `requirements.txt` a été mis à jour (29/07/2026) avec des
versions qui ont des wheels officielles pour Python 3.14 :

| Package | Avant (cassé) | Après (corrigé) |
|---|---|---|
| `numpy` | 2.0.0 | **2.3.4** (support 3.14 depuis 2.3.0) |
| `polars` | 1.0.0 | **1.32.0** (support 3.14 officiel) |
| `lightgbm` | 4.4.0 | **4.6.0** (support 3.14 depuis 4.6) |
| `optuna` | 4.0.0 | **4.2.0** (support 3.14 depuis 4.2) |

Si vous avez déjà un venv `.venv` avec l'ancienne config, supprimez-le et
recréez-le :

```bash
# Dans Git Bash
rm -rf .venv
py -3.14 -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

**Si une autre dépendance tente de se compiler** (ex. `pandas`, `pyarrow`,
`scipy`), deux options :

1. **Installer Visual C++ Build Tools** : https://visualstudio.microsoft.com/visual-cpp-build-tools/ — cochez "Desktop development with C++" (~6 Go).
2. **Utiliser WSL2** (cf. Option A plus haut) — Linux a ses propres
   compilateurs préinstallés et toutes les wheels sont disponibles.

Pour vérifier si une wheel existe pour votre version Python :
```bash
# Lister les wheels disponibles pour numpy sur PyPI
py -3.14 -m pip install --dry-run numpy==2.3.4 --only-binary :all:
# Si "Could not find a version that satisfies the requirement"
# → pas de wheel pour votre plateforme
```

### EOL / CRLF : `ruff check` échoue

### Polars trop lent sur Windows natif

Polars utilise le filesystem Windows qui est plus lent que Linux.
Pour de grosses données (>1 Go), passez par WSL2.

### EOL / CRLF : `ruff check` échoue

Configurez Git pour ne pas convertir les EOL :
```bash
git config --global core.autocrlf false
# ou pour ce repo seulement :
git config core.autocrlf false
```

Puis :
```bash
git rm --cached -r .
git reset --hard
```

### `npm install` échoue

Sur Windows natif, certains packages natifs nécessitent Visual C++
Build Tools (cf. plus haut). Alternative : utilisez WSL2.

---

## 📚 Documentation associée

- `README.md` — Vue d'ensemble
- `docs/PLAN_DIRECTEUR_AMELIORATIONS.md` — Plan d'amélioration
- `docs/FIN_JINJA2.md` — Frontend officiel unique (Next.js)
- `PRODUCTION_READINESS.md` — Checklist Go/No-Go pour le live réel
- `docs/MIGRATION_OKX.md` — Migration Binance → OKX (MiCA)

---

## ✅ Checklist de premier démarrage

- [ ] Python 3.14 installé et dans le PATH
- [ ] Node.js 20+ installé
- [ ] Git Bash ou WSL2 opérationnel
- [ ] `bash scripts/setup.sh` terminé sans erreur
- [ ] `.env` créé avec une `WEB_API_KEY`
- [ ] `python cli.py --paper` démarre sans erreur
- [ ] UI accessible sur `http://127.0.0.1:8000`
- [ ] Frontend Next.js démarre sur `http://localhost:3000`
- [ ] Premier backtest réussi : `python cli.py --backtest BTC/USDC --limit 500`

Bienvenue dans le bot-crypto ! 🚀
