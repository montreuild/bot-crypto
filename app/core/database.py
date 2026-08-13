"""
Base de données SQLite étendue — trades, métriques journalières, signaux, params optimizer.
"""
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    case,
    create_engine,
    event,
    func,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

logger = logging.getLogger(__name__)
Base   = declarative_base()


class Trade(Base):
    __tablename__ = "trades"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    time        = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    symbol      = Column(String(20))
    side        = Column(String(10))
    strategy    = Column(String(50))
    score       = Column(Float)
    entry       = Column(Float)
    exit_price  = Column(Float)
    stop        = Column(Float)
    size        = Column(Float)
    notional    = Column(Float)
    pnl         = Column(Float)
    pnl_pct     = Column(Float)
    fees        = Column(Float)
    # FIN-06 : décomposition de `fees` par catégorie (agrégats via
    # get_fee_breakdown ci-dessous). `fee_taker`+`fee_maker` == `fees` — les
    # deux existent séparément de `fees` pour rester rétro-compatibles avec
    # tout code lisant encore l'agrégat historique.
    fee_taker   = Column(Float, default=0.0)
    fee_maker   = Column(Float, default=0.0)
    borrow_cost = Column(Float, default=0.0)
    leverage    = Column(Float, default=1.0)
    status      = Column(String(30))
    duration_bars = Column(Integer)
    entry_time  = Column(DateTime)
    exit_time   = Column(DateTime)
    timeframe   = Column(String(10))
    reason      = Column(Text)
    # FIN-06 : motif de CLÔTURE (stop_loss/trailing_stop/take_profit/
    # exit_after_bars/manual/...), distinct de `reason` qui reste le motif
    # d'OUVERTURE (signal de la stratégie) — les deux étaient conflatés
    # auparavant (`reason` écrasé par le motif de sortie anticipée seulement).
    exit_reason = Column(String(30))
    tags        = Column(JSON)
    # ── L1 (§29) — sorties partielles ───────────────────────────────────────
    # `exits` : jambes sorties avant la clôture finale, chacune avec son prix,
    # sa fraction et son PnL. `pnl` porte déjà leur total ; cette colonne dit
    # COMMENT il a été fait, ce qu'un montant agrégé ne peut pas dire.
    exits        = Column(JSON)
    realized_pnl = Column(Float, default=0.0)
    size_initial = Column(Float)
    # ── L0 (§99) — journal : contexte de décision et coûts ventilés ─────────
    gross_pnl     = Column(Float)
    slippage_cost = Column(Float, default=0.0)
    funding_cost  = Column(Float, default=0.0)
    mfe           = Column(Float)
    mae           = Column(Float)
    setup         = Column(String(40))
    module        = Column(String(40))
    session_name  = Column(String(20))
    htf_bias      = Column(String(20))
    structure_state = Column(String(30))
    sequence_type = Column(String(30))
    tier          = Column(String(2))
    net_rr        = Column(Float)
    __table_args__ = (
        Index("ix_trades_symbol", "symbol"),
        Index("ix_trades_strategy", "strategy"),
        Index("ix_trades_time", "time"),
        Index("ix_trades_symbol_strategy", "symbol", "strategy"),
        # OPS-09 : couvre get_closed_trades_for_slot/get_slot_live_stats
        # (filtres strategy + timeframe + time >=) — sinon scan complet.
        Index("ix_trades_strategy_tf_time", "strategy", "timeframe", "time"),
    )


class DailyStats(Base):
    __tablename__ = "daily_stats"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    date        = Column(String(10), unique=True)
    trades      = Column(Integer, default=0)
    wins        = Column(Integer, default=0)
    pnl         = Column(Float, default=0.0)
    fees        = Column(Float, default=0.0)
    max_dd      = Column(Float, default=0.0)
    equity_open = Column(Float)
    equity_close= Column(Float)


class Signal(Base):
    __tablename__ = "signals"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    time       = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    symbol     = Column(String(20))
    strategy   = Column(String(50))
    side       = Column(String(10))
    score      = Column(Float)
    acted      = Column(Boolean, default=False)
    reason     = Column(Text)
    indicators = Column(JSON)


class OptimizerResult(Base):
    __tablename__ = "optimizer_results"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    time       = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    strategy   = Column(String(50))
    params     = Column(JSON)
    score      = Column(Float)
    sharpe     = Column(Float)
    win_rate   = Column(Float)
    total_pnl  = Column(Float)
    oos_score  = Column(Float)   # out-of-sample score


class OpenPosition(Base):
    """Position ouverte persistée en BDD (récupérée après crash/redémarrage)."""
    __tablename__ = "open_positions"
    id           = Column(String(100), primary_key=True)   # "{symbol}::{strategy}::{tf}"
    symbol       = Column(String(20),  nullable=False)
    side         = Column(String(10),  nullable=False)
    strategy     = Column(String(50))
    score        = Column(Float)
    entry        = Column(Float,       nullable=False)
    stop         = Column(Float,       nullable=False)
    size         = Column(Float,       nullable=False)
    notional     = Column(Float)
    leverage     = Column(Float,       default=1.0)
    open_time    = Column(Float,       nullable=False)   # timestamp Unix
    fees         = Column(Float,       default=0.0)
    order_id     = Column(String(100), default="")
    reason       = Column(Text,        default="")
    __table_args__ = (
        Index("ix_open_positions_symbol", "symbol"),
    )


class SlotLifecycleEvent(Base):
    """Transition d'état du cycle de vie d'un bot (Phase 2).

    États : candidat → essai → actif → retiré (et retours). L'état courant d'un
    slot est le ``to_state`` de son événement le plus récent.
    """
    __tablename__ = "slot_lifecycle_events"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    time      = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    slot_key  = Column(String(100), nullable=False)
    from_state = Column(String(20))
    to_state   = Column(String(20), nullable=False)
    reason     = Column(Text, default="")
    score      = Column(Float)
    budget_pct = Column(Float)
    __table_args__ = (
        Index("ix_lifecycle_slot", "slot_key"),
        Index("ix_lifecycle_time", "time"),
    )


class RiskStateRow(Base):
    """État de risque persisté (Phase 3) pour une reprise propre après crash.

    Une seule ligne (``id='global'``) pour l'état global ; une ligne par slot
    (``id='slot::{slot_key}'``) pour les pauses CB. Stockage JSON souple.
    """
    __tablename__ = "risk_state"
    id      = Column(String(120), primary_key=True)
    updated = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                     onupdate=lambda: datetime.now(timezone.utc))
    data    = Column(JSON)


def _migrate_schema(engine):
    """Migrations idempotentes (OPS-08) : colonnes et index manquants.

    ``create_all`` ignore entièrement une table déjà existante : toute colonne
    (ou index) ajoutée au modèle après la création d'une base n'y apparaîtrait
    jamais — l'INSERT suivant échouerait. On compare donc le schéma réel
    (``PRAGMA table_info``) aux modèles et on exécute des
    ``ALTER TABLE … ADD COLUMN`` idempotents, puis on crée les index manquants
    (``checkfirst`` — ex. ``ix_trades_strategy_tf_time``, OPS-09).

    Les colonnes ajoutées le sont **nullables sans défaut SQL** (contrainte
    SQLite sur ADD COLUMN) : les défauts Python des modèles restent appliqués
    par l'ORM aux nouvelles lignes.
    """
    if engine.dialect.name != "sqlite":
        return
    from sqlalchemy import text
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            rows = conn.execute(
                text(f'PRAGMA table_info("{table.name}")')).fetchall()
            if not rows:          # table absente : create_all vient de la créer
                continue
            existing = {r[1] for r in rows}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl_type = col.type.compile(engine.dialect)
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}'))
                logger.info(
                    f"[DB] Migration : colonne {table.name}.{col.name} "
                    f"({ddl_type}) ajoutée.")
    for table in Base.metadata.sorted_tables:
        for idx in table.indexes:
            idx.create(bind=engine, checkfirst=True)


def init_db(url: str = "sqlite:///crypto_bot.db"):
    """Initialise la base de données et retourne (engine, SessionLocal)."""
    is_sqlite = url.startswith("sqlite")
    # timeout/busy_timeout : le bot écrit depuis plusieurs threads (cycle live,
    # retrains ML, jobs optimizer, API). Sans WAL + busy_timeout, SQLite lève
    # « database is locked » sous écritures concurrentes en prod.
    connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
    engine = create_engine(url, connect_args=connect_args)

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _conn_record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")      # lecteurs concurrents + 1 writer
            cur.execute("PRAGMA synchronous=NORMAL")    # bon compromis durabilité/perf
            cur.execute("PRAGMA busy_timeout=30000")    # attend au lieu d'échouer (30s)
            cur.close()

    Base.metadata.create_all(engine)
    _migrate_schema(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    logger.info(f"[DB] Connecté : {url}")
    return engine, SessionLocal


@contextmanager
def session_scope(SessionLocal):
    """Context manager garantissant la fermeture de la session en toutes circonstances.

    Les fonctions de database.py (persist_open_position, save_trade, etc.) gèrent
    elles-mêmes commit/rollback — session_scope se contente de fermer proprement.

    Usage :
        with session_scope(self.SessionLocal) as sess:
            persist_open_position(sess, pos)
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def persist_open_position(session: Session, pos: dict) -> None:
    """Sauvegarde ou met à jour une position ouverte en BDD."""
    _required = ("id", "symbol", "side", "entry", "stop", "size")
    missing = [k for k in _required if k not in pos]
    if missing:
        raise KeyError(f"persist_open_position: clés manquantes {missing}")
    existing = session.get(OpenPosition, pos["id"])
    if existing:
        existing.stop     = pos["stop"]
        existing.fees     = pos.get("fees", 0.0)
        existing.notional = pos.get("notional", 0.0)
    else:
        rec = OpenPosition(
            id        = pos["id"],
            symbol    = pos["symbol"],
            side      = pos["side"],
            strategy  = pos.get("strategy", ""),
            score     = pos.get("score", 0.0),
            entry     = pos["entry"],
            stop      = pos["stop"],
            size      = pos["size"],
            notional  = pos.get("notional", 0.0),
            leverage  = pos.get("leverage", 1.0),
            open_time = pos.get("open_time", 0.0),
            fees      = pos.get("fees", 0.0),
            order_id  = pos.get("order_id", ""),
            reason    = pos.get("reason", ""),
        )
        session.add(rec)
    try:
        session.commit()
    except Exception as e:
        logger.warning(f"[DB] persist_open_position commit KO : {e}")
        session.rollback()
        raise


def delete_open_position(session: Session, pos_id: str) -> None:
    """Supprime une position de la table open_positions (appelé à la clôture)."""
    rec = session.get(OpenPosition, pos_id)
    if rec:
        session.delete(rec)
        try:
            session.commit()
        except Exception as e:
            logger.warning(f"[DB] delete_open_position commit KO : {e}")
            session.rollback()
            raise


def load_open_positions(session: Session) -> List[dict]:
    """Charge les positions ouvertes depuis la BDD au démarrage."""
    rows = session.query(OpenPosition).all()
    result = []
    for r in rows:
        result.append({
            "id":        r.id,
            "symbol":    r.symbol,
            "side":      r.side,
            "strategy":  r.strategy,
            "score":     r.score,
            "entry":     r.entry,
            "stop":      r.stop,
            "size":      r.size,
            "notional":  r.notional,
            "leverage":  r.leverage,
            "open_time": r.open_time,
            "fees":      r.fees,
            "order_id":  r.order_id,
            "reason":    r.reason,
            "pnl":       0.0,
            "_trailing": None,   # sera réinitialisé dans live_trader
        })
    return result


def save_trade(session: Session, t: dict):
    # FIN-06 : si l'appelant ne fournit pas déjà la répartition taker/maker,
    # replie tout l'agrégat `fees` sur `fee_taker` — c'est la réalité actuelle
    # du chemin live (aucune distinction maker/taker implémentée côté
    # exécution, cf. docstring PositionMixin._close_position) : mieux vaut
    # une répartition honnête (100% taker) qu'une colonne `fee_maker` vide de
    # sens par défaut.
    fees = t.get("fees", 0) or 0
    fee_taker = t.get("fee_taker", fees)
    fee_maker = t.get("fee_maker", 0)
    # S4-11 (audit V2) : entry_time était défini en colonne (database.py:56)
    # mais JAMAIS renseigné → impossible d'analyser la durée des positions
    # ouvertes vs fermées, ou de calculer des métriques de holding time.
    # On le remplit depuis `t['entry_time']` si fourni (format ISO str ou
    # datetime), sinon on dérive depuis `t['time']` (close) moins
    # `t['duration_bars']` × TF (approximation).
    entry_time = t.get("entry_time")
    if entry_time is None and t.get("duration_bars") is not None and t.get("time") is not None:
        try:
            from datetime import datetime as _dt
            from datetime import timedelta as _td

            from app.core.timeframes import TF_MINUTES
            tf = t.get("timeframe", "1h")
            mins_per_bar = TF_MINUTES.get(tf, 60)
            close_time = t["time"]
            if isinstance(close_time, str):
                close_time = _dt.fromisoformat(close_time.replace("Z", ""))
            entry_time = close_time - _td(minutes=int(t["duration_bars"]) * mins_per_bar)
        except Exception:
            entry_time = None
    rec = Trade(
        symbol=t.get("symbol"), side=t.get("side"), strategy=t.get("strategy"),
        score=t.get("score"), entry=t.get("entry"), exit_price=t.get("exit"),
        stop=t.get("stop"), size=t.get("size"), notional=t.get("notional"),
        pnl=t.get("pnl"), pnl_pct=t.get("pnl_pct"), fees=fees,
        fee_taker=fee_taker, fee_maker=fee_maker,
        borrow_cost=t.get("borrow_cost",0), leverage=t.get("leverage",1),
        status=t.get("status"), duration_bars=t.get("duration_bars"),
        timeframe=t.get("timeframe"), reason=t.get("reason",""),
        exit_reason=t.get("exit_reason"), tags=t.get("tags"),
        entry_time=entry_time,  # S4-11 : désormais renseigné
        # L1 / L0 — jambes partielles et contexte de décision. Toutes
        # optionnelles : un trade qui ne les porte pas écrit des NULL.
        exits=t.get("exits") or None,
        realized_pnl=t.get("realized_pnl", 0) or 0,
        size_initial=t.get("size_initial"),
        gross_pnl=t.get("gross_pnl"),
        slippage_cost=t.get("slippage_cost", 0) or 0,
        funding_cost=t.get("funding_cost", 0) or 0,
        mfe=t.get("mfe"), mae=t.get("mae"),
        setup=t.get("setup"), module=t.get("module"),
        session_name=t.get("session"), htf_bias=t.get("htf_bias"),
        structure_state=t.get("structure_state"),
        sequence_type=t.get("sequence_type"),
        tier=t.get("tier"), net_rr=t.get("net_rr"),
    )
    try:
        session.add(rec)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return rec


def get_trades(session: Session, limit=1000, symbol=None, strategy=None,
               since: Optional[datetime] = None) -> List[Trade]:
    q = session.query(Trade)
    if symbol:
        q = q.filter(Trade.symbol == symbol)
    if strategy:
        q = q.filter(Trade.strategy == strategy)
    if since is not None:
        q = q.filter(Trade.time >= since)
    return q.order_by(Trade.time.desc()).limit(limit).all()


def get_trade_global_aggregates(session: Session, since: Optional[datetime] = None) -> dict:
    """Agrégats globaux (S4-06) calculés par SQL (COUNT/SUM/MAX) — évite de
    charger jusqu'à 10 000 lignes ``Trade`` en objets Python pour un simple
    total. Consommé par ``HealthMixin._load_db_stats`` pour les compteurs
    globaux ; le détail par stratégie (Sharpe/drawdown, qui a besoin de la
    séquence ORDONNÉE des PnL) continue de charger les lignes via
    ``get_trades`` — non exprimable en agrégats SQL simples."""
    q = session.query(
        func.count(Trade.id).label("total_trades"),
        func.coalesce(func.sum(Trade.pnl), 0.0).label("total_pnl"),
        func.coalesce(func.sum(Trade.fees), 0.0).label("total_fees"),
        func.coalesce(func.max(Trade.pnl), 0.0).label("best_trade"),
        func.coalesce(func.sum(case((Trade.pnl > 0, 1), else_=0)), 0).label("wins"),
        func.coalesce(func.sum(case((Trade.pnl > 0, Trade.pnl), else_=0.0)), 0.0).label("gross_win"),
        func.coalesce(func.sum(case((Trade.pnl < 0, Trade.pnl), else_=0.0)), 0.0).label("gross_loss_signed"),
    )
    if since is not None:
        q = q.filter(Trade.time >= since)
    row = q.one()
    return {
        "total_trades": int(row.total_trades or 0),
        "total_pnl":    float(row.total_pnl or 0.0),
        "total_fees":   float(row.total_fees or 0.0),
        "best_trade":   float(row.best_trade or 0.0),
        "wins":         int(row.wins or 0),
        "gross_win":    float(row.gross_win or 0.0),
        "gross_loss":   abs(float(row.gross_loss_signed or 0.0)),
    }


_STOP_EXIT_REASONS = ("stop_loss", "trailing_stop")


def get_fee_breakdown(session: Session, since: Optional[datetime] = None) -> dict:
    """FIN-06 : compteur de frais par catégorie — taker/maker/borrow/stop.

    ``taker``/``maker`` : répartition de ``fees`` (hors emprunt) par colonnes
    dédiées (``fee_taker``/``fee_maker``, cf. modèle ``Trade``).
    ``borrow`` : ``SUM(borrow_cost)`` (déjà séparé du reste, colonne existante).
    ``stop`` : ``SUM(fees)`` des seuls trades clos par ``exit_reason`` in
    (stop_loss, trailing_stop) — mesure DIAGNOSTIC (quelle part des frais vient
    de sorties subies plutôt que voulues), pas une catégorie disjointe de
    taker/maker (un exit stop est lui-même taker OU maker)."""
    q = session.query(
        func.coalesce(func.sum(Trade.fee_taker), 0.0).label("taker"),
        func.coalesce(func.sum(Trade.fee_maker), 0.0).label("maker"),
        func.coalesce(func.sum(Trade.borrow_cost), 0.0).label("borrow"),
        func.coalesce(
            func.sum(case((Trade.exit_reason.in_(_STOP_EXIT_REASONS), Trade.fees), else_=0.0)),
            0.0,
        ).label("stop"),
    )
    if since is not None:
        q = q.filter(Trade.time >= since)
    row = q.one()
    return {
        "taker":  float(row.taker or 0.0),
        "maker":  float(row.maker or 0.0),
        "borrow": float(row.borrow or 0.0),
        "stop":   float(row.stop or 0.0),
    }


def get_closed_trades_for_slot(session: Session, strategy: str, timeframe: str,
                               days: int = 45, symbol: str = None) -> List[Trade]:
    """Trades réels **fermés** d'un slot ``strategy::timeframe[::symbol]`` sur les
    ``days`` derniers jours, du plus récent au plus ancien.

    Utilisé par le forward-test glissant pour comparer la réalisation live à la
    fourchette Monte-Carlo simulée. Ne renvoie que les trades avec un PnL connu
    (``status`` commençant par ``closed``). ``symbol`` optionnel : restreint au
    symbole du slot (configs par symbole).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    q = session.query(Trade).filter(
        Trade.strategy == strategy,
        Trade.timeframe == timeframe,
        Trade.status.like("closed%"),
        Trade.pnl.isnot(None),
        Trade.time >= cutoff,
    )
    if symbol:
        q = q.filter(Trade.symbol == symbol)
    return q.order_by(Trade.time.desc()).all()


def get_slot_live_stats(session: Session, strategy: str, timeframe: str,
                        days: int = 30, symbol: str = None) -> dict:
    """Stats live agrégées d'un bot ``strategy::timeframe[::symbol]`` sur
    ``days`` jours. ``symbol`` optionnel : restreint au symbole du slot.

    Budget-indépendant : on remonte le nombre de trades, le win-rate et le
    **rendement moyen par trade (%)**, exploités par la machine à états du cycle
    de vie (Phase 2). Retourne des zéros si aucun trade.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    q = session.query(Trade.pnl, Trade.pnl_pct).filter(
        Trade.strategy == strategy,
        Trade.timeframe == timeframe,
        Trade.status.like("closed%"),
        Trade.pnl.isnot(None),
        Trade.time >= cutoff,
    )
    if symbol:
        q = q.filter(Trade.symbol == symbol)
    rows = q.all()
    n = len(rows)
    if n == 0:
        return {"n_trades": 0, "wins": 0, "win_rate": 0.0,
                "total_pnl": 0.0, "avg_return_pct": 0.0}
    wins = sum(1 for r in rows if (r.pnl or 0) > 0)
    total_pnl = sum((r.pnl or 0) for r in rows)
    pcts = [r.pnl_pct for r in rows if r.pnl_pct is not None]
    avg_ret = (sum(pcts) / len(pcts)) if pcts else 0.0
    return {
        "n_trades": n,
        "wins": wins,
        "win_rate": round(wins / n * 100, 2),
        "total_pnl": round(total_pnl, 6),
        "avg_return_pct": round(avg_ret, 4),
    }


def record_lifecycle_event(session: Session, slot_key: str, from_state: Optional[str],
                           to_state: str, reason: str = "", score: float = None,
                           budget_pct: float = None) -> None:
    """Persiste une transition d'état du cycle de vie d'un bot."""
    rec = SlotLifecycleEvent(
        slot_key=slot_key, from_state=from_state, to_state=to_state,
        reason=reason, score=score, budget_pct=budget_pct,
    )
    try:
        session.add(rec)
        session.commit()
    except Exception as e:
        logger.warning(f"[DB] record_lifecycle_event KO ({slot_key}) : {e}")
        session.rollback()


def get_current_lifecycle_states(session: Session) -> Dict[str, str]:
    """État courant (dernier ``to_state``) de chaque slot ayant un historique."""
    rows = session.query(SlotLifecycleEvent).order_by(
        SlotLifecycleEvent.time.asc()
    ).all()
    states: Dict[str, str] = {}
    for r in rows:
        states[r.slot_key] = r.to_state
    return states


# ── Persistance de l'état de risque (Phase 3) ────────────────────────────────
def save_risk_state(session: Session, key: str, data: dict) -> None:
    """Upsert d'un blob d'état de risque (``key`` = 'global' ou 'slot::...')."""
    try:
        row = session.get(RiskStateRow, key)
        if row is None:
            row = RiskStateRow(id=key, data=data)
            session.add(row)
        else:
            row.data = data
        session.commit()
    except Exception as e:
        logger.warning(f"[DB] save_risk_state KO ({key}) : {e}")
        session.rollback()


def load_risk_state(session: Session, key: str = None) -> dict:
    """Charge l'état de risque : un blob si ``key`` fourni, sinon tout le dict."""
    try:
        if key is not None:
            row = session.get(RiskStateRow, key)
            return dict(row.data) if row and row.data else {}
        return {r.id: dict(r.data or {}) for r in session.query(RiskStateRow).all()}
    except Exception as e:
        logger.warning(f"[DB] load_risk_state KO : {e}")
        return {}


def update_daily_stats(session: Session, date_str: str, pnl: float, win: bool,
                       fees: float, equity: float):
    row = session.query(DailyStats).filter(DailyStats.date == date_str).first()
    if not row:
        row = DailyStats(date=date_str, trades=0, wins=0, pnl=0.0,
                         fees=0.0, equity_open=equity, equity_close=equity)
        session.add(row)
    # Protection NoneType si colonnes NULL en DB (migration depuis version antérieure)
    row.trades      = (row.trades  or 0) + 1
    row.wins        = (row.wins    or 0) + (1 if win else 0)
    row.pnl         = round((row.pnl   or 0.0) + pnl,  6)
    row.fees        = round((row.fees  or 0.0) + fees, 6)
    row.equity_close = equity
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
