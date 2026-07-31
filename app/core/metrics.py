"""OBS-01 — métriques Prometheus.

L'observabilité était le poste le plus faible de l'audit (~4/10) : le bot dit
ce qu'il fait dans un fichier de log, et rien d'autre. Pour répondre à « depuis
quand la latence de l'API a-t-elle doublé ? » ou « combien de positions
ouvertes hier à 3h ? », il fallait grepper `bot.log`. Ce module expose l'état
sous une forme interrogeable dans le temps.

**Dépendance optionnelle, comme `optuna` et `psutil`.** Sans
`prometheus-client`, chaque fonction d'ici est un no-op et `/metrics` répond
503 en disant quoi installer. Un module d'observabilité qui empêche le bot de
démarrer serait une régression de disponibilité vendue comme un progrès.

**Un seul point de branchement côté métier.** Les compteurs de trading ne sont
pas semés dans `live_trader`, `position_manager` et le reste : ils sont dérivés
des événements que ces modules publient DÉJÀ sur `app.core.events.EventHub`.
Le hub voit passer chaque ouverture, chaque clôture, chaque signal et chaque
événement de risque — l'instrumenter une fois vaut mieux que trente appels
`inc()` à maintenir, et garantit que métriques et flux WebSocket racontent la
même histoire.

**Cardinalité.** Les libellés sont bornés par construction : stratégies,
symboles, timeframes, TEMPLATES de route (`/api/ml/registry/versions`, jamais
l'URL concrète avec ses paramètres). Une métrique labellisée par une valeur
libre — un id de job, un chemin brut — fait exploser la mémoire du serveur
Prometheus ; c'est le piège classique et il est évité ici délibérément.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:  # pragma: no cover - dépend de l'environnement
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    AVAILABLE = True
except ImportError:  # pragma: no cover
    AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; charset=utf-8"

#: Registre DÉDIÉ plutôt que le registre global par défaut. Deux raisons :
#: les tests peuvent instancier un état propre, et un import parasite de
#: `prometheus_client` ailleurs dans le processus ne vient pas polluer la
#: sortie du bot avec ses propres collecteurs.
REGISTRY: Any = CollectorRegistry() if AVAILABLE else None

_M: Dict[str, Any] = {}

#: Buckets de latence HTTP en secondes. Les défauts de `prometheus_client`
#: s'arrêtent à 10 s ; ici un backtest ou un entraînement lancé en synchrone
#: dépasse allègrement, et tout finirait dans `+Inf` — donc invisible.
_HTTP_BUCKETS = (0.005, 0.025, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300)


def _build() -> None:
    """Déclare les collecteurs. Idempotent — un second appel ne fait rien.

    Redéclarer un collecteur sur le même registre lève `ValueError`
    (`Duplicated timeseries`), ce qui transformerait un double import en
    crash au démarrage.
    """
    if not AVAILABLE or _M:
        return

    _M["http_requests"] = Counter(
        "cryptobot_http_requests_total",
        "Requêtes HTTP servies par l'API.",
        ["method", "route", "status"], registry=REGISTRY)
    _M["http_latency"] = Histogram(
        "cryptobot_http_request_duration_seconds",
        "Latence de traitement des requêtes HTTP.",
        ["method", "route"], buckets=_HTTP_BUCKETS, registry=REGISTRY)

    _M["trades_opened"] = Counter(
        "cryptobot_trades_opened_total",
        "Positions ouvertes.",
        ["strategy", "symbol", "side", "timeframe"], registry=REGISTRY)
    _M["trades_closed"] = Counter(
        "cryptobot_trades_closed_total",
        "Positions clôturées, par issue (`win`/`loss`/`flat`) et motif de sortie.",
        ["symbol", "side", "outcome", "reason"], registry=REGISTRY)
    _M["trade_pnl"] = Histogram(
        "cryptobot_trade_pnl_pct",
        "Distribution du PnL de sortie, en pourcentage.",
        ["symbol", "side"],
        buckets=(-20, -10, -5, -2, -1, 0, 1, 2, 5, 10, 20), registry=REGISTRY)
    _M["fees"] = Counter(
        "cryptobot_fees_total",
        "Frais cumulés payés à la clôture (devise de cotation).",
        ["symbol"], registry=REGISTRY)

    _M["signals"] = Counter(
        "cryptobot_signals_total",
        "Signaux produits par le pipeline, acceptés ou rejetés.",
        ["strategy_slot", "symbol", "timeframe", "side", "accepted"],
        registry=REGISTRY)
    _M["risk_events"] = Counter(
        "cryptobot_risk_events_total",
        "Événements de risque (circuit breaker, drawdown, marge).",
        ["subtype", "severity"], registry=REGISTRY)

    _M["capital"] = Gauge(
        "cryptobot_capital",
        "Capital courant tel que publié par le cycle de trading.",
        registry=REGISTRY)
    _M["open_positions"] = Gauge(
        "cryptobot_open_positions",
        "Positions ouvertes au dernier cycle.", registry=REGISTRY)
    _M["cycle_duration"] = Histogram(
        "cryptobot_scan_duration_seconds",
        "Durée du scan par cycle.",
        buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120), registry=REGISTRY)
    _M["cycles"] = Counter(
        "cryptobot_cycles_total", "Cycles de trading exécutés.", registry=REGISTRY)

    _M["ml_trainings"] = Counter(
        "cryptobot_ml_trainings_total",
        "Entraînements ML terminés, par décision de gate.",
        ["recipe", "tf", "decision"], registry=REGISTRY)
    _M["ml_train_duration"] = Histogram(
        "cryptobot_ml_train_duration_seconds",
        "Durée d'un entraînement ML.",
        ["recipe", "tf"],
        buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800), registry=REGISTRY)

    logger.info(f"[Metrics] {len(_M)} collecteurs Prometheus déclarés")


_build()


def _labels(metric: str, *values: Any):
    """Accès défensif à un collecteur libellé.

    L'observabilité ne doit jamais faire tomber ce qu'elle observe : une
    métrique mal libellée est un bug d'affichage, pas une raison d'interrompre
    un trade. D'où le `try` large et le retour `None`.
    """
    m = _M.get(metric)
    if m is None:
        return None
    try:
        return m.labels(*[str(v) for v in values]) if values else m
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Metrics] libellé {metric} rejeté : {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP
# ─────────────────────────────────────────────────────────────────────────────
def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    """Enregistre une requête servie. ``route`` est un TEMPLATE, pas une URL."""
    c = _labels("http_requests", method, route, status)
    if c is not None:
        c.inc()
    h = _labels("http_latency", method, route)
    if h is not None:
        h.observe(seconds)


# ─────────────────────────────────────────────────────────────────────────────
#  ML
# ─────────────────────────────────────────────────────────────────────────────
def observe_training(recipe: str, tf: str, decision: str, seconds: float) -> None:
    c = _labels("ml_trainings", recipe or "?", tf or "?", decision or "?")
    if c is not None:
        c.inc()
    h = _labels("ml_train_duration", recipe or "?", tf or "?")
    if h is not None:
        h.observe(seconds)


# ─────────────────────────────────────────────────────────────────────────────
#  Métier — dérivé des événements du hub
# ─────────────────────────────────────────────────────────────────────────────
def _outcome(pnl: float) -> str:
    return "win" if pnl > 0 else "loss" if pnl < 0 else "flat"


def record_event(event: Dict[str, Any]) -> None:
    """Traduit un événement de ``EventHub`` en métriques.

    Appelée depuis ``EventHub.publish`` — donc sur le chemin chaud du trading.
    Elle ne lève jamais et ne bloque jamais : les opérations Prometheus en
    mémoire sont de l'ordre de la microseconde, mais l'enveloppe `try` reste
    là parce qu'un événement malformé ne doit pas remonter dans le trader.
    """
    if not AVAILABLE:
        return
    try:
        etype = str(event.get("type", ""))
        d = event.get("data") or {}

        if etype == "trade.opened":
            c = _labels("trades_opened", d.get("strategy", "?"), d.get("symbol", "?"),
                        d.get("side", "?"), d.get("timeframe", "?"))
            if c is not None:
                c.inc()

        elif etype == "trade.closed":
            pnl = float(d.get("pnl") or 0.0)
            c = _labels("trades_closed", d.get("symbol", "?"), d.get("side", "?"),
                        _outcome(pnl), d.get("reason") or "unknown")
            if c is not None:
                c.inc()
            h = _labels("trade_pnl", d.get("symbol", "?"), d.get("side", "?"))
            if h is not None:
                h.observe(float(d.get("pnl_pct") or 0.0))
            fees = float(d.get("fees") or 0.0)
            if fees:
                f = _labels("fees", d.get("symbol", "?"))
                if f is not None:
                    f.inc(fees)

        elif etype == "signal.generated":
            c = _labels("signals", d.get("slot_key", "?"), d.get("symbol", "?"),
                        d.get("timeframe", "?"), d.get("side", "?"),
                        bool(d.get("accepted")))
            if c is not None:
                c.inc()

        elif etype.startswith("risk."):
            c = _labels("risk_events", etype.split(".", 1)[1],
                        d.get("severity", "warning"))
            if c is not None:
                c.inc()

        elif etype == "cycle.update":
            g = _M.get("capital")
            if g is not None:
                g.set(float(d.get("capital") or 0.0))
            g = _M.get("open_positions")
            if g is not None:
                g.set(int(d.get("open_positions") or 0))
            h = _M.get("cycle_duration")
            if h is not None:
                h.observe(float(d.get("scan_duration_ms") or 0.0) / 1000.0)
            c = _M.get("cycles")
            if c is not None:
                c.inc()
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Metrics] événement ignoré : {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Rendu
# ─────────────────────────────────────────────────────────────────────────────
def render() -> Tuple[Optional[bytes], str]:
    """``(payload, content_type)`` — ``payload`` vaut ``None`` sans la lib."""
    if not AVAILABLE:
        return None, CONTENT_TYPE_LATEST
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


class timed:
    """Chronomètre de bloc : ``with timed() as t: ...`` puis ``t.seconds``.

    Utilise ``perf_counter`` et non ``time()`` : une correction NTP pendant un
    entraînement d'une heure produirait sinon des durées négatives.
    """

    __slots__ = ("_t0", "seconds")

    def __enter__(self) -> "timed":
        self._t0 = time.perf_counter()
        self.seconds = 0.0
        return self

    def __exit__(self, *exc: Any) -> None:
        self.seconds = time.perf_counter() - self._t0
