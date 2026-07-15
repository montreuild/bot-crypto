#!/usr/bin/env bash
# ==============================================================================
# setup.sh — Installation et démarrage du Crypto Bot (Python backend + Next.js frontend)
#
# Compatibilité :
#   - Linux (Ubuntu 22.04+, Debian, Fedora, Arch)
#   - macOS (Intel + Apple Silicon)
#   - Windows via Git Bash (https://git-scm.com) ou WSL2 (recommandé)
#
# Usage :
#   bash setup.sh            # installation complète (backend + frontend)
#   bash setup.sh --backend  # backend Python uniquement
#   bash setup.sh --frontend # frontend Next.js uniquement (après backend)
#   bash setup.sh --run      # démarre backend + frontend après install
#   bash setup.sh --paper    # démarre en paper mode (sans clés API)
#
# Prérequis :
#   - Python 3.12+ (https://www.python.org/downloads/)
#   - Node.js 20+ (https://nodejs.org/)
#   - Git
#
# Aucune modification de config.yaml n'est effectuée par ce script.
# ==============================================================================

set -euo pipefail

# ── Couleurs (désactivées si pas de TTY) ─────────────────────────────────────
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
    PURPLE='\033[0;35m'; CYAN='\033[0;36m'; NC='\033[0m'; BOLD='\033[1m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; PURPLE=''; CYAN=''; NC=''; BOLD=''
fi

log()     { echo -e "${BLUE}[setup]${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC} $*"; }
error()   { echo -e "${RED}✗${NC} $*" >&2; }
section() { echo -e "\n${BOLD}${PURPLE}═══ $* ═══${NC}\n"; }

# ── Détection OS ─────────────────────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Linux*)  OS="linux";;
        Darwin*) OS="macos";;
        MINGW*|MSYS*|CYGWIN*) OS="windows";;
        *)       OS="unknown";;
    esac
    ARCH=$(uname -m 2>/dev/null || echo "x86_64")
    log "OS détecté : $OS ($ARCH)"
}

# ── Vérification des prérequis ───────────────────────────────────────────────
check_python() {
    section "Vérification Python 3.12+"
    local py_cmd=""
    for cmd in python3.12 python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
            local major=${ver%%.*}
            local minor=${ver#*.}
            if [ "$major" = "3" ] && [ "$minor" -ge 12 ]; then
                py_cmd="$cmd"
                success "Python $ver trouvé ($cmd)"
                echo "$cmd"
                return 0
            fi
        fi
    done
    error "Python 3.12+ requis mais non trouvé."
    cat <<EOF

${YELLOW}Installation de Python 3.12 :${NC}

${BOLD}Linux (Ubuntu 22.04)${NC}:
  sudo add-apt-repository ppa:deadsnakes/ppa -y
  sudo apt update && sudo apt install -y python3.12 python3.12-venv python3.12-dev

${BOLD}Linux (Ubuntu 24.04+)${NC}:
  Python 3.12 est déjà natif.

${BOLD}macOS${NC}:
  brew install python@3.12

${BOLD}Windows${NC}:
  1. Téléchargez Python 3.12 depuis https://www.python.org/downloads/
  2. Lors de l'installation, cochez "Add Python to PATH"
  3. Redémarrez Git Bash / PowerShell
  4. Relancez ce script

EOF
    return 1
}

check_node() {
    section "Vérification Node.js 20+"
    if ! command -v node &>/dev/null; then
        warn "Node.js non trouvé (requis pour le frontend Next.js)"
        cat <<EOF

${YELLOW}Installation de Node.js :${NC}

${BOLD}Linux/macOS (via nvm — recommandé)${NC}:
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.0/install.sh | bash
  source ~/.bashrc
  nvm install 20
  nvm use 20

${BOLD}Windows${NC}:
  Téléchargez Node.js 20+ LTS depuis https://nodejs.org/

${YELLOW}Le backend Python peut tourner sans Node.js.
Vous pourrez installer le frontend plus tard avec : bash setup.sh --frontend${NC}

EOF
        read -rp "Continuer avec backend seulement ? [O/n] " continue_backend
        if [[ "$continue_backend" =~ ^[Nn] ]]; then
            return 1
        fi
        return 0
    fi
    local ver
    ver=$(node --version | grep -oE '[0-9]+' | head -1)
    if [ "$ver" -lt 20 ]; then
        warn "Node.js $ver détecté, 20+ recommandé. Le frontend pourrait avoir des soucis."
    else
        success "Node.js $(node --version) trouvé"
    fi
}

check_git() {
    if ! command -v git &>/dev/null; then
        error "Git non trouvé. Installez-le :"
        case "$OS" in
            linux)  echo "  sudo apt install git  # Debian/Ubuntu" ;;
            macos)  echo "  brew install git       # ou Xcode Command Line Tools" ;;
            windows) echo "  https://git-scm.com/download/win" ;;
        esac
        return 1
    fi
    success "Git trouvé ($(git --version))"
}

# ── Installation backend ─────────────────────────────────────────────────────
install_backend() {
    section "Installation du backend Python"

    local py_cmd
    py_cmd=$(check_python) || exit 1

    # Détection de la racine du projet (répertoire parent du dossier scripts/)
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
    cd "$PROJECT_DIR"
    log "Répertoire projet : $PROJECT_DIR"

    if [ ! -f "requirements.txt" ]; then
        error "requirements.txt introuvable. Êtes-vous dans le bon repo ?"
        exit 1
    fi

    # Création du venv
    if [ ! -d ".venv" ]; then
        log "Création du venv Python 3.12..."
        "$py_cmd" -m venv .venv
        success "Venv créé"
    else
        success "Venv existant (.venv)"
    fi

    # Activation
    case "$OS" in
        windows)
            # Git Bash sur Windows : Scripts/activate au lieu de bin/activate
            if [ -f ".venv/Scripts/activate" ]; then
                # shellcheck disable=SC1091
                source .venv/Scripts/activate
            else
                # Fallback WSL
                # shellcheck disable=SC1091
                source .venv/bin/activate
            fi
            ;;
        *)
            # shellcheck disable=SC1091
            source .venv/bin/activate
            ;;
    esac

    # Upgrade pip
    log "Mise à jour de pip..."
    python -m pip install --upgrade pip wheel setuptools --quiet

    # Installation dépendances
    log "Installation des dépendances Python (cela peut prendre 2-5 min)..."
    pip install -r requirements.txt --quiet
    success "Dépendances Python installées"

    # Vérification
    if python -c "import fastapi, polars, ccxt" 2>/dev/null; then
        success "Imports critiques OK (fastapi, polars, ccxt)"
    else
        error "Import critique échoué — vérifiez requirements.txt"
        exit 1
    fi

    # Premier lancement de test (compilation only)
    log "Vérification de l'import du bot..."
    if python -c "from app.api.main import app; print('FastAPI app OK')" 2>&1; then
        success "Backend fonctionnel"
    else
        warn "L'import a émis des warnings (souvent normaux en première install)"
    fi

    # Lancer la suite de tests (optionnel)
    if [ "${SKIP_TESTS:-0}" != "1" ]; then
        read -rp "Lancer la suite de tests pytest ? (~30 s, recommandé) [O/n] " run_tests
        if [[ ! "$run_tests" =~ ^[Nn] ]]; then
            log "Exécution de pytest (peut prendre 30-60 s)..."
            if python -m pytest -x -q --tb=short 2>&1 | tail -20; then
                success "Tests passés"
            else
                warn "Certains tests ont échoué — le bot peut quand même tourner, mais à investiguer"
            fi
        fi
    fi

    echo
    success "Backend prêt ! Démarrez avec :"
    echo
    echo -e "  ${CYAN}source .venv/bin/activate${NC}  # (ou .venv\\Scripts\\activate sur Windows)"
    echo -e "  ${CYAN}python cli.py --paper${NC}"
    echo
    echo -e "  ${YELLOW}→ Interface Jinja2 : http://localhost:8000${NC}"
    echo -e "  ${YELLOW}→ Interface Next.js : http://localhost:3000 (après setup.sh --frontend)${NC}"
}

# ── Installation frontend ────────────────────────────────────────────────────
install_frontend() {
    section "Installation du frontend Next.js"

    check_node || return 0

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
    cd "$PROJECT_DIR/frontend"

    if [ ! -f "package.json" ]; then
        error "frontend/package.json introuvable. Avez-vous bien cloné le repo ?"
        return 1
    fi

    # Détection du package manager
    local pkg_mgr="npm"
    if command -v pnpm &>/dev/null; then
        pkg_mgr="pnpm"
        success "pnpm détecté (rapide)"
    elif command -v bun &>/dev/null; then
        pkg_mgr="bun"
        success "bun détecté (très rapide)"
    else
        warn "npm utilisé (installez pnpm pour aller plus vite : npm i -g pnpm)"
    fi

    log "Installation des dépendances frontend (3-5 min avec npm)..."
    case "$pkg_mgr" in
        npm)  npm install --silent ;;
        pnpm) pnpm install ;;
        bun)  bun install ;;
    esac
    success "Dépendances frontend installées"

    echo
    success "Frontend prêt ! Démarrez avec :"
    echo
    echo -e "  ${CYAN}cd frontend${NC}"
    echo -e "  ${CYAN}npm run dev${NC}  # (ou pnpm dev / bun dev)"
    echo
    echo -e "  ${YELLOW}→ Interface Next.js : http://localhost:3000${NC}"
    echo -e "  ${YELLOW}→ Le backend FastAPI doit tourner sur http://localhost:8000${NC}"
}

# ── Démarrage ────────────────────────────────────────────────────────────────
run_bot() {
    section "Démarrage du bot"
    local mode="${1:-paper}"

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
    cd "$PROJECT_DIR"

    if [ ! -d ".venv" ]; then
        error "Venv inexistant. Lancez d'abord : bash setup.sh --backend"
        exit 1
    fi

    # Activation
    case "$OS" in
        windows) source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate ;;
        *)       source .venv/bin/activate ;;
    esac

    if [ "$mode" = "paper" ]; then
        log "Démarrage en PAPER MODE (sans clés API)..."
        log "Backend FastAPI : http://localhost:8000"
        log "UI Jinja2       : http://localhost:8000"
        log ""
        log "Pour l'UI Next.js, dans un autre terminal :"
        echo -e "  ${CYAN}cd frontend && npm run dev${NC}"
        echo
        python cli.py --paper
    else
        log "Démarrage normal (mode défini dans config.yaml)..."
        python cli.py
    fi
}

# ── Aide ─────────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
${BOLD}setup.sh — Crypto Bot installer${NC}

${BOLD}Usage :${NC}
  bash setup.sh [option]

${BOLD}Options :${NC}
  (aucune)       Installation complète (backend + frontend)
  --backend      Installer uniquement le backend Python
  --frontend     Installer uniquement le frontend Next.js
  --run          Démarrer le bot en mode normal (après install)
  --paper        Démarrer le bot en paper mode (sans clés API)
  --help, -h     Afficher cette aide

${BOLD}Exemples :${NC}
  bash setup.sh              # Tout installer
  bash setup.sh --paper      # Tout installer + démarrer en paper mode
  bash setup.sh --backend    # Backend seul (sans Node.js)
  bash setup.sh --frontend   # Frontend seul (si backend déjà installé)

${BOLD}Environnement :${NC}
  SKIP_TESTS=1   Ne pas lancer pytest après install backend

${BOLD}URLs après démarrage :${NC}
  Backend FastAPI (Jinja2 UI) : http://localhost:8000
  Frontend Next.js             : http://localhost:3000
  API docs (Swagger)           : http://localhost:8000/api/docs
  Health check                 : http://localhost:8000/health
EOF
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    echo -e "${BOLD}${CYAN}"
    cat <<'BANNER'
   ██████╗ ██████╗ ███████╗██╗   ██╗ ██████╗ ███████╗███╗   ██╗
  ██╔════╝ ██╔══██╗██╔════╝██║   ██║██╔═══██╗██╔════╝████╗  ██║
  ██║  ███╗██████╔╝███████╗██║   ██║██║   ██║███████╗██╔██╗ ██║
  ██║   ██║██╔══██╗╚════██║██║   ██║██║   ██║╚════██║██║╚██╗██║
  ╚██████╔╝██║  ██║███████║╚██████╔╝╚██████╔╝███████║██║ ╚████║
   ╚═════╝ ╚═╝  ╚═╝╚══════╝ ╚═════╝  ╚═════╝ ╚══════╝╚═╝  ╚═══╝
                     Setup script v1.0
BANNER
    echo -e "${NC}"

    detect_os
    check_git

    local arg="${1:-all}"
    case "$arg" in
        --backend)  install_backend ;;
        --frontend) install_frontend ;;
        --run)      run_bot normal ;;
        --paper)    install_backend && install_frontend && run_bot paper ;;
        --help|-h)  usage ;;
        *)
            # Par défaut : tout installer
            install_backend
            echo
            read -rp "Installer aussi le frontend Next.js ? [O/n] " install_fe
            if [[ ! "$install_fe" =~ ^[Nn] ]]; then
                install_frontend
            fi
            section "Installation terminée"
            success "Tout est prêt !"
            echo
            echo -e "Pour démarrer en paper mode :  ${CYAN}bash setup.sh --paper${NC}"
            echo -e "Pour démarrer normalement :    ${CYAN}bash setup.sh --run${NC}"
            ;;
    esac
}

main "$@"
