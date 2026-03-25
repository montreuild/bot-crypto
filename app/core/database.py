"""
Base de données SQLite étendue — trades, métriques journalières, signaux, params optimizer.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy import (create_engine, Column, Integer, Float, String,
                        Boolean, DateTime, Text, JSON, Index)
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


def init_db(url: str = "sqlite:///crypto_bot.db"):
    """Initialise la base de données et retourne (engine, SessionLocal)."""
    engine     = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    logger.info(f"[DB] Connecté : {url}")
    return engine, SessionLocal


def persist_open_position(session: Session, pos: dict) -> None:
    """Sauvegarde ou met à jour une position ouverte en BDD."""
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
    except Exception:
        session.rollback()
        raise


def delete_open_position(session: Session, pos_id: str) -> None:
    """Supprime une position de la table open_positions (appelé à la clôture)."""
    rec = session.get(OpenPosition, pos_id)
    if rec:
        session.delete(rec)
        try:
            session.commit()
        except Exception:
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
