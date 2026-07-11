"""
Base de données SQLite étendue — trades, métriques journalières, signaux, params optimizer.
"""
import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy import (create_engine, event, Column, Integer, Float, String,
                        Boolean, DateTime, Text, JSON, Index, func)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

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
    borrow_cost = Column(Float, default=0.0)
    leverage    = Column(Float, default=1.0)
    status      = Column(String(30))
    duration_bars = Column(Integer)
    entry_time  = Column(DateTime)
    exit_time   = Column(DateTime)
    timeframe   = Column(String(10))
    reason      = Column(Text)
    tags        = Column(JSON)
    __table_args__ = (
        Index("ix_trades_symbol", "symbol"),
        Index("ix_trades_strategy", "strategy"),
        Index("ix_trades_time", "time"),
        Index("ix_trades_symbol_strategy", "symbol", "strategy"),
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
    rec = Trade(
        symbol=t.get("symbol"), side=t.get("side"), strategy=t.get("strategy"),
        score=t.get("score"), entry=t.get("entry"), exit_price=t.get("exit"),
        stop=t.get("stop"), size=t.get("size"), notional=t.get("notional"),
        pnl=t.get("pnl"), pnl_pct=t.get("pnl_pct"), fees=t.get("fees",0),
        borrow_cost=t.get("borrow_cost",0), leverage=t.get("leverage",1),
        status=t.get("status"), duration_bars=t.get("duration_bars"),
        timeframe=t.get("timeframe"), reason=t.get("reason",""), tags=t.get("tags"),
    )
    try:
        session.add(rec)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return rec


def get_trades(session: Session, limit=1000, symbol=None, strategy=None) -> List[Trade]:
    q = session.query(Trade)
    if symbol:   q = q.filter(Trade.symbol == symbol)
    if strategy: q = q.filter(Trade.strategy == strategy)
    return q.order_by(Trade.time.desc()).limit(limit).all()


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
                        days: int = 30) -> dict:
    """Stats live agrégées d'un bot ``strategy::timeframe`` sur ``days`` jours.

    Budget-indépendant : on remonte le nombre de trades, le win-rate et le
    **rendement moyen par trade (%)**, exploités par la machine à états du cycle
    de vie (Phase 2). Retourne des zéros si aucun trade.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.query(Trade.pnl, Trade.pnl_pct).filter(
        Trade.strategy == strategy,
        Trade.timeframe == timeframe,
        Trade.status.like("closed%"),
        Trade.pnl.isnot(None),
        Trade.time >= cutoff,
    ).all()
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


def get_lifecycle_events(session: Session, slot_key: str = None,
                         limit: int = 200) -> List[dict]:
    """Historique des transitions (toutes ou filtrées par slot), plus récentes d'abord."""
    q = session.query(SlotLifecycleEvent)
    if slot_key:
        q = q.filter(SlotLifecycleEvent.slot_key == slot_key)
    rows = q.order_by(SlotLifecycleEvent.time.desc()).limit(limit).all()
    return [{
        "time": r.time.isoformat() if r.time else None,
        "slot_key": r.slot_key, "from_state": r.from_state, "to_state": r.to_state,
        "reason": r.reason, "score": r.score, "budget_pct": r.budget_pct,
    } for r in rows]


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


# ── Persistance de l'état de l'allocateur de capital (stats hebdo) ───────────
def save_allocator_state(session: Session, data: dict) -> None:
    """Persiste l'état de l'allocateur (stats hebdo par slot + prochain rebalance).

    Réutilise la table ``risk_state`` (blob JSON souple) sous la clé
    ``'allocator'`` — évite les pertes au redémarrage (sinon le rééquilibrage
    par profit-factor repart de zéro et ignore la semaine écoulée)."""
    save_risk_state(session, "allocator", data)


def load_allocator_state(session: Session) -> dict:
    """Charge l'état persisté de l'allocateur (vide si absent)."""
    return load_risk_state(session, "allocator")


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
