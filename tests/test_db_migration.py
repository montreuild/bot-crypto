"""OPS-08/OPS-09 — migrations SQLite idempotentes + index composite.

Acceptation OPS-08 : sur une trades.db existante dont le schéma est en retard
sur le modèle (colonnes manquantes), ``init_db`` ajoute les colonnes sans
erreur et l'écriture ORM fonctionne.
Acceptation OPS-09 : ``EXPLAIN QUERY PLAN`` de la requête slot utilise
``ix_trades_strategy_tf_time`` (pas de SCAN de la table).
"""
import sqlite3

from sqlalchemy import text

from app.core.database import init_db, save_trade, session_scope


def _legacy_db(path) -> None:
    """Crée une base « ancienne » : table trades sans tags/borrow_cost/leverage
    ni l'index composite (schéma d'avant ces ajouts au modèle)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time DATETIME NOT NULL,
            symbol VARCHAR(20), side VARCHAR(10), strategy VARCHAR(50),
            score FLOAT, entry FLOAT, exit_price FLOAT, stop FLOAT,
            size FLOAT, notional FLOAT, pnl FLOAT, pnl_pct FLOAT,
            fees FLOAT, status VARCHAR(30), duration_bars INTEGER,
            entry_time DATETIME, exit_time DATETIME, timeframe VARCHAR(10),
            reason TEXT
        );
        CREATE INDEX ix_trades_symbol ON trades (symbol);
        INSERT INTO trades (time, symbol, strategy, timeframe, status, pnl)
        VALUES ('2026-01-01 00:00:00', 'BTC/USDC', 'trend_rider', '1h',
                'closed_tp', 12.5);
    """)
    conn.commit()
    conn.close()


def test_migrate_adds_missing_columns_and_index(tmp_path):
    db = tmp_path / "trades.db"
    _legacy_db(db)

    engine, SessionLocal = init_db(f"sqlite:///{db}")
    with engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text('PRAGMA table_info("trades")'))}
        assert {"tags", "borrow_cost", "leverage"} <= cols
        # FIN-06 : décomposition des frais + motif de clôture, ajoutés au
        # modèle après la création de cette base « ancienne ».
        assert {"fee_taker", "fee_maker", "exit_reason"} <= cols
        idx = {r[1] for r in conn.execute(text('PRAGMA index_list("trades")'))}
        assert "ix_trades_strategy_tf_time" in idx
        # La ligne héritée reste lisible.
        n = conn.execute(text("SELECT COUNT(*) FROM trades")).scalar()
        assert n == 1

    # L'écriture ORM (toutes colonnes du modèle) fonctionne sur la base migrée.
    with session_scope(SessionLocal) as sess:
        save_trade(sess, {
            "symbol": "ETH/USDC", "side": "long", "strategy": "smart_money",
            "timeframe": "4h", "status": "closed_sl", "entry": 100.0,
            "exit_price": 99.0, "size": 1.0, "notional": 100.0,
            "pnl": -1.0, "pnl_pct": -1.0, "fees": 0.1,
            "tags": {"slot": "smart_money::4h::ETH/USDC"},
        })
    engine.dispose()


def test_migrate_is_idempotent(tmp_path):
    db = tmp_path / "trades.db"
    _legacy_db(db)
    for _ in range(2):        # deux démarrages successifs : aucune erreur
        engine, _ = init_db(f"sqlite:///{db}")
        engine.dispose()


def test_slot_query_uses_composite_index(tmp_path):
    db = tmp_path / "trades.db"
    engine, _ = init_db(f"sqlite:///{db}")
    with engine.connect() as conn:
        plan = "\n".join(str(r) for r in conn.execute(text(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM trades WHERE strategy='trend_rider' "
            "AND timeframe='1h' AND status LIKE 'closed%' "
            "AND pnl IS NOT NULL AND time >= '2026-01-01'"
        )))
        assert "ix_trades_strategy_tf_time" in plan
        assert "SCAN trades" not in plan
    engine.dispose()
