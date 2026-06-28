#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  oracle-setup.sh — Déploiement Crypto Bot sur Oracle Free Tier
#
#  Testé sur : Oracle Cloud Always Free (Ubuntu 22.04 / 24.04)
#    - VM Ampere A1 : 4 vCPU ARM64, 24 GB RAM  (recommandé)
#    - VM AMD64 micro : 1 OCPU, 1 GB RAM  (ressources limitées)
#
#  Usage :
#    chmod +x deploy/oracle-setup.sh
#    sudo ./deploy/oracle-setup.sh
#
#  Ce script :
#    1. Installe Python 3.12, Nginx, Certbot, outils système
#    2. Crée l'utilisateur applicatif et le dossier /opt/crypto_bot
#    3. Crée le venv Python et installe les dépendances
#    4. Configure le pare-feu (ufw + iptables Oracle)
#    5. Installe et active le service systemd
#    6. Installe Nginx en reverse proxy
#    7. Affiche les étapes post-installation (clés API, SSL)
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ── Variables — adapter si nécessaire ───────────────────────────
APP_DIR="/opt/crypto_bot"
APP_USER="botuser"
VENV_DIR="$APP_DIR/.venv"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # racine du dépôt cloné
LOG_DIR="$APP_DIR/logs"
SERVICE_NAME="crypto-bot"

# ── Helpers ─────────────────────────────────────────────────────
info()  { echo -e "\e[36m[INFO]\e[0m  $*"; }
ok()    { echo -e "\e[32m[ OK ]\e[0m  $*"; }
warn()  { echo -e "\e[33m[WARN]\e[0m  $*"; }
err()   { echo -e "\e[31m[ERR ]\e[0m  $*" >&2; exit 1; }

# ── Vérification root ────────────────────────────────────────────
[[ $EUID -eq 0 ]] || err "Ce script doit être exécuté en root : sudo $0"

# ════════════════════════════════════════════════════════════════
# 1. Mise à jour système + installation des paquets
# ════════════════════════════════════════════════════════════════
info "Mise à jour des paquets système…"
apt-get update -qq
apt-get upgrade -y -qq

info "Installation de Python 3.12, Nginx, outils…"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    software-properties-common curl wget git unzip \
    build-essential libssl-dev libffi-dev \
    nginx certbot python3-certbot-nginx \
    ufw apache2-utils

# Python 3.12 (natif sur Ubuntu 24.04, via PPA sur 22.04)
if ! python3.12 --version &>/dev/null; then
    info "Ajout du PPA deadsnakes pour Python 3.12…"
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        python3.12 python3.12-venv python3.12-dev python3.12-distutils
fi
ok "Python $(python3.12 --version) installé"

# ════════════════════════════════════════════════════════════════
# 2. Création de l'utilisateur applicatif
# ════════════════════════════════════════════════════════════════
if ! id "$APP_USER" &>/dev/null; then
    info "Création de l'utilisateur $APP_USER…"
    useradd --system --shell /bin/bash --create-home --home-dir "$APP_DIR" "$APP_USER"
    ok "Utilisateur $APP_USER créé"
else
    ok "Utilisateur $APP_USER existe déjà"
fi

# ════════════════════════════════════════════════════════════════
# 3. Copie des fichiers de l'application
# ════════════════════════════════════════════════════════════════
info "Copie de l'application dans $APP_DIR…"
rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
      --exclude='*.pyc' --exclude='logs/' --exclude='trades.db' \
      "$REPO_DIR/" "$APP_DIR/"

mkdir -p "$LOG_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
ok "Fichiers copiés"

# ════════════════════════════════════════════════════════════════
# 4. Création du venv et installation des dépendances
# ════════════════════════════════════════════════════════════════
info "Création du venv Python 3.12…"
sudo -u "$APP_USER" python3.12 -m venv "$VENV_DIR"

info "Installation des dépendances Python…"
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade pip -q
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" -q
ok "Dépendances installées"

# ════════════════════════════════════════════════════════════════
# 5. Fichier .env (clés API)
# ════════════════════════════════════════════════════════════════
ENV_FILE="$APP_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    info "Création du fichier .env (à compléter manuellement)…"
    cat > "$ENV_FILE" <<'EOF'
# ── Clés API échange (OKX) ──────────────────────────────────────
# Remplir avant de démarrer le bot en mode LIVE
# OKX exige une passphrase API en plus de la clé/secret
OKX_API_KEY=
OKX_API_SECRET=
OKX_API_PASSWORD=

# ── Clé API du dashboard web (générer une clé forte) ────────────
# Exemple : openssl rand -hex 32
BOT_API_KEY=

# ── Notifications (optionnel) ───────────────────────────────────
# TELEGRAM_TOKEN=
# TELEGRAM_CHAT_ID=
EOF
    chmod 600 "$ENV_FILE"
    chown "$APP_USER:$APP_USER" "$ENV_FILE"
    warn "Complétez $ENV_FILE avec vos clés API avant de démarrer le bot"
else
    ok "Fichier .env existant conservé"
fi

# ════════════════════════════════════════════════════════════════
# 6. Pare-feu : ufw + règles iptables Oracle
#    Oracle Cloud bloque les ports entrants par défaut via iptables
#    (en plus des Security Lists dans la console OCI).
#    Ouvrir les ports 22 (SSH), 80 (HTTP), 443 (HTTPS).
# ════════════════════════════════════════════════════════════════
info "Configuration du pare-feu ufw…"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP Nginx'
ufw allow 443/tcp comment 'HTTPS Nginx'
ufw --force enable
ok "ufw configuré"

# Règles iptables supplémentaires Oracle (persistées via /etc/iptables/rules.v4)
info "Application des règles iptables Oracle…"
iptables -I INPUT -p tcp --dport 80  -j ACCEPT
iptables -I INPUT -p tcp --dport 443 -j ACCEPT
if command -v iptables-save &>/dev/null; then
    mkdir -p /etc/iptables
    iptables-save > /etc/iptables/rules.v4
    # Restauration automatique via iptables-persistent
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables-persistent
fi
ok "iptables configuré"

# ════════════════════════════════════════════════════════════════
# 7. Service systemd
# ════════════════════════════════════════════════════════════════
info "Installation du service systemd $SERVICE_NAME…"
# Adapter le service pour l'utilisateur botuser
sed "s/^User=ubuntu/User=$APP_USER/" "$APP_DIR/deploy/crypto-bot.service" \
    | sed "s/^Group=ubuntu/Group=$APP_USER/" \
    > /etc/systemd/system/${SERVICE_NAME}.service

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
ok "Service $SERVICE_NAME installé et activé au démarrage"

# ════════════════════════════════════════════════════════════════
# 8. Nginx — reverse proxy (HTTP uniquement, HTTPS via Certbot)
# ════════════════════════════════════════════════════════════════
info "Configuration Nginx…"
NGINX_SITE="/etc/nginx/sites-available/$SERVICE_NAME"
cat > "$NGINX_SITE" <<'NGINX'
server {
    listen 80;
    server_name _;

    # Authentification basique (protège le dashboard)
    # Créer le fichier : sudo htpasswd -c /etc/nginx/.htpasswd botuser
    # auth_basic           "Crypto Bot — Accès restreint";
    # auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_connect_timeout 60s;
        proxy_send_timeout    300s;
        proxy_read_timeout    300s;
    }

    location /api/optimize/stream {
        proxy_pass             http://127.0.0.1:8000;
        proxy_http_version     1.1;
        proxy_set_header       Connection "";
        proxy_buffering        off;
        proxy_cache            off;
        proxy_read_timeout     600s;
        chunked_transfer_encoding on;
    }

    access_log /var/log/nginx/crypto-bot-access.log;
    error_log  /var/log/nginx/crypto-bot-error.log;
}
NGINX

ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
ok "Nginx configuré"

# ════════════════════════════════════════════════════════════════
# Résumé et étapes suivantes
# ════════════════════════════════════════════════════════════════
IP=$(curl -s --max-time 5 https://ifconfig.me 2>/dev/null \
    || curl -s --max-time 5 https://api.ipify.org 2>/dev/null \
    || { warn "Impossible de détecter l'IP publique — vérifiez la console OCI"; hostname -I | awk '{print $1}'; })

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  ✅  Installation terminée"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "  Adresse IP publique : $IP"
echo "  Dashboard accessible : http://$IP/"
echo ""
echo "  ── Étapes obligatoires avant démarrage ──────────────────────"
echo "  1. Compléter $ENV_FILE :"
echo "       OKX_API_KEY=..."
echo "       OKX_API_SECRET=..."
echo "       OKX_API_PASSWORD=...   # passphrase API OKX"
echo "       BOT_API_KEY=\$(openssl rand -hex 32)"
echo ""
echo "  2. Vérifier/adapter $APP_DIR/config.yaml :"
echo "       paper_mode: true   ← laisser true pour commencer"
echo ""
echo "  3. Démarrer le bot :"
echo "       sudo systemctl start $SERVICE_NAME"
echo "       sudo journalctl -fu $SERVICE_NAME"
echo ""
echo "  ── HTTPS (optionnel mais recommandé) ────────────────────────"
echo "  Si vous avez un domaine pointant sur $IP :"
echo "    sudo certbot --nginx -d votre-domaine.com"
echo ""
echo "  ── Authentification dashboard (recommandé) ──────────────────"
echo "  sudo htpasswd -c /etc/nginx/.htpasswd botuser"
echo "  Puis décommenter les lignes auth_basic dans $NGINX_SITE"
echo "  sudo nginx -t && sudo systemctl reload nginx"
echo ""
echo "  ── Commandes utiles ─────────────────────────────────────────"
echo "  sudo systemctl status  $SERVICE_NAME"
echo "  sudo systemctl restart $SERVICE_NAME"
echo "  sudo tail -f $LOG_DIR/bot.log"
echo "════════════════════════════════════════════════════════════════"
