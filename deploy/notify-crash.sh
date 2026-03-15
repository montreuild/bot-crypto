#!/bin/bash
# ─────────────────────────────────────────────────────────────
# notify-crash.sh — Wrapper shell pour notify-crash.py
# Appelé par systemd ExecStopPost (les variables EXIT_CODE etc.
# sont automatiquement passées en variables d'environnement).
# ─────────────────────────────────────────────────────────────
/usr/bin/python3 /opt/crypto_bot/deploy/notify-crash.py
