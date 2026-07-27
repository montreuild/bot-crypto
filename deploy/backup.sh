#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  backup.sh — Sauvegarde datée de trades.db + config.yaml + strategies/*.yaml
#
#  Remplace les one-liners cron ad hoc de DEPLOY.md §9 par un script
#  unique et idempotent. Utilise l'API sqlite3.backup() de Python (pas un
#  simple `cp`, ni une dépendance au binaire CLI `sqlite3` non installé par
#  oracle-setup.sh) pour trades.db car la base tourne en WAL (PRAGMA
#  journal_mode=WAL, cf. app/core/database.py) — une copie de fichier brute
#  peut manquer les pages encore dans trades.db-wal et produire une
#  sauvegarde incohérente sous écriture concurrente.
#
#  Usage :
#    chmod +x deploy/backup.sh
#    ./deploy/backup.sh                    # backup manuel
#
#  Installation (cron quotidien à 03h00, garde les 7 derniers jours) :
#    (crontab -l 2>/dev/null; echo "0 3 * * * /opt/crypto_bot/deploy/backup.sh \
#      >> /opt/crypto_bot/logs/backup.log 2>&1") | crontab -
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ── SEC-008 : permissions restrictives ──────────────────────────
#  Ce que ce script recopie n'est pas anodin : config.yaml porte les clés API
#  exchange et les tokens de notification, trades.db l'historique complet des
#  positions. Avec le umask par défaut (022) les archives sortaient en 0644,
#  donc lisibles par tout compte de la machine — la sauvegarde était un
#  contournement des permissions du fichier d'origine.
#
#  umask 077 couvre TOUT ce qui est créé ensuite, y compris le fichier écrit
#  par le sous-processus Python (sqlite3.backup()) que l'on ne peut pas
#  chmod-er avant son existence. Les chmod explicites plus bas ne sont donc
#  pas redondants par excès de prudence : ils rattrapent aussi les archives
#  déjà présentes, créées par une version antérieure de ce script.
umask 077

# ── Variables — adapter si nécessaire ───────────────────────────
APP_DIR="${APP_DIR:-/opt/crypto_bot}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
DATE_TAG="$(date +%Y%m%d_%H%M%S)"
PYTHON_BIN="${PYTHON_BIN:-$APP_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

# ── Helpers ─────────────────────────────────────────────────────
info()  { echo "[INFO]  $*"; }
ok()    { echo "[ OK ]  $*"; }
warn()  { echo "[WARN]  $*"; }

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# ── trades.db (sqlite3.backup() — cohérent même sous écriture WAL) ─
DB_PATH="$APP_DIR/trades.db"
if [[ -f "$DB_PATH" ]]; then
    DB_OUT="$BACKUP_DIR/trades_${DATE_TAG}.db"
    "$PYTHON_BIN" -c "
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
src.close()
dst.close()
" "$DB_PATH" "$DB_OUT"
    chmod 600 "$DB_OUT"
    ok "trades.db -> $DB_OUT"
else
    warn "trades.db introuvable ($DB_PATH), backup ignoré"
fi

# ── config.yaml ───────────────────────────────────────────────────
CONFIG_PATH="$APP_DIR/config.yaml"
if [[ -f "$CONFIG_PATH" ]]; then
    cp "$CONFIG_PATH" "$BACKUP_DIR/config_${DATE_TAG}.yaml"
    chmod 600 "$BACKUP_DIR/config_${DATE_TAG}.yaml"
    ok "config.yaml -> $BACKUP_DIR/config_${DATE_TAG}.yaml"
else
    warn "config.yaml introuvable ($CONFIG_PATH), backup ignoré"
fi

# ── strategies/*.yaml (archive unique par run) ───────────────────
STRATEGIES_DIR="$APP_DIR/strategies"
if [[ -d "$STRATEGIES_DIR" ]]; then
    STRAT_OUT="$BACKUP_DIR/strategies_${DATE_TAG}.tar.gz"
    tar -czf "$STRAT_OUT" -C "$APP_DIR" strategies
    chmod 600 "$STRAT_OUT"
    ok "strategies/*.yaml -> $STRAT_OUT"
else
    warn "strategies/ introuvable ($STRATEGIES_DIR), backup ignoré"
fi

# ── SEC-008 : rattrapage des archives créées avant ce correctif ──
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'trades_*.db' -o -name 'config_*.yaml' -o -name 'strategies_*.tar.gz' \) \
    ! -perm 600 -exec chmod 600 {} + 2>/dev/null || true

# ── Rétention : purge des sauvegardes plus vieilles que RETENTION_DAYS ─
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'trades_*.db' -o -name 'config_*.yaml' -o -name 'strategies_*.tar.gz' \) \
    -mtime "+$RETENTION_DAYS" -print -delete | while read -r f; do
    info "purgé (> ${RETENTION_DAYS}j) : $f"
done

ok "Backup terminé ($DATE_TAG), rétention ${RETENTION_DAYS}j."
