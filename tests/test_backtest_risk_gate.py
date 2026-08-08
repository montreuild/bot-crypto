"""Tests du BacktestRiskGate (QW-6 / étape 6).

Vérifie que les circuit breakers du mode realistic_risk fonctionnent :
- consecutive_loss_limit → pause slot
- slot_daily_dd_limit → pause slot
- max_trades_per_day → refus
- kill-switch global (DD global > seuil) → halt
- volatility_brake → factor ×0.5
- reset() réinitialise l'état
- to_diagnostics() expose l'état pour audit

Couvre l'exigence 3 « close to live » — le backtest doit appliquer les
mêmes circuit breakers que le live pour évaluer la robustesse d'une
stratégie face aux pauses de slot.
"""
from app.engine.backtest_risk_gate import BacktestRiskGate


def test_from_config_lit_les_memes_cles_que_le_gate_live():
    """Parité de configuration avec `app/core/risk_gate.py`.

    Régression : le gate backtest lisait `risk.global_dd_limit` et
    `risk.volatility_brake_threshold`, deux clés que PERSONNE n'écrit. Le live
    lit `trading.max_drawdown_global` et `risk.volatility_threshold`. Un
    opérateur qui abaissait son DD global à 15 % voyait donc le backtest
    continuer à tourner sur 20 % — et les deux ne se comparaient plus.
    """
    cfg = {
        "trading": {
            "daily_drawdown_limit": 0.04,
            "max_drawdown_global": 0.15,
        },
        "risk": {
            "consecutive_loss_limit": 5,
            "slot_daily_dd_limit": 0.05,
            "max_trades_per_day": 10,
            "volatility_threshold": 0.04,
        },
    }
    gate = BacktestRiskGate.from_config(cfg, timeframe="1h")
    assert gate.consec_loss_limit == 5
    assert gate.slot_daily_dd_limit == 0.05
    assert gate.max_trades_per_day == 10
    assert gate.daily_dd_limit == 0.04
    assert gate.global_dd_limit == 0.15
    assert gate.volatility_threshold == 0.04


def test_from_config_convertit_la_pause_du_live_en_bougies():
    """La pause « pertes consécutives » du live est en SECONDES.

    Régression : `pause_bars` était figé à 24 bougies, soit 24 h sur du 1h
    contre 30 minutes en live — un backtest 48× plus punitif que le bot qu'il
    simule. On la dérive maintenant de `risk.consecutive_pause_secs`.
    """
    cfg = {"risk": {"consecutive_pause_secs": 1800}}
    # 1800 s sur des bougies de 15 min → 2 bougies.
    assert BacktestRiskGate.from_config(cfg, timeframe="15m").pause_bars == 2
    # Sur du 1h, la pause est plus courte que la bougie : plancher à 1.
    assert BacktestRiskGate.from_config(cfg, timeframe="1h").pause_bars == 1
    # `bars_per_day` suit le timeframe, pour le garde-fou des pauses journalières.
    assert BacktestRiskGate.from_config(cfg, timeframe="1h").bars_per_day == 24
    assert BacktestRiskGate.from_config(cfg, timeframe="15m").bars_per_day == 96


def test_from_config_sans_risk_cfg_retourne_defauts():
    """Sans config risk, on retourne les défauts."""
    gate = BacktestRiskGate.from_config({})
    assert gate.consec_loss_limit == 3
    assert gate.slot_daily_dd_limit == 0.03
    assert gate.max_trades_per_day == 0


def test_can_slot_trade_ok_par_defaut():
    """Sans circuit breaker déclenché, can_slot_trade retourne (True, '')."""
    gate = BacktestRiskGate()
    ok, reason = gate.can_slot_trade(
        i=0, slot_key="trend@1h@BTC/USDC", day_key="2024-01-01",
        current_capital=1000, peak_capital=1000,
    )
    assert ok is True
    assert reason == ""


def test_consecutive_loss_limit_pause_slot():
    """3 pertes consécutives → slot pausé."""
    gate = BacktestRiskGate(consec_loss_limit=3, pause_bars=24)
    slot_key = "trend@1h@BTC/USDC"

    # 3 pertes consécutives
    gate.record_trade_result(i=0, slot_key=slot_key, pnl=-10, day_key="2024-01-01", capital_before=1000)
    assert gate._slots[slot_key].consecutive_losses == 1
    assert not gate._slots[slot_key].paused

    gate.record_trade_result(i=1, slot_key=slot_key, pnl=-10, day_key="2024-01-01", capital_before=1000)
    assert gate._slots[slot_key].consecutive_losses == 2
    assert not gate._slots[slot_key].paused

    gate.record_trade_result(i=2, slot_key=slot_key, pnl=-10, day_key="2024-01-01", capital_before=1000)
    assert gate._slots[slot_key].consecutive_losses == 3
    assert gate._slots[slot_key].paused
    assert "pertes consécutives" in gate._slots[slot_key].pause_reason
    assert gate._slots[slot_key].pause_until_bar == 2 + 24

    # Tentative d'entrée → refus
    ok, reason = gate.can_slot_trade(
        i=3, slot_key=slot_key, day_key="2024-01-01",
        current_capital=970, peak_capital=1000,
    )
    assert ok is False
    assert "pausé" in reason.lower()


def test_consecutive_loss_reset_par_gain():
    """Un gain reset le compteur de pertes consécutives."""
    gate = BacktestRiskGate(consec_loss_limit=3)
    slot_key = "trend@1h@BTC/USDC"

    gate.record_trade_result(i=0, slot_key=slot_key, pnl=-10, day_key="2024-01-01", capital_before=1000)
    gate.record_trade_result(i=1, slot_key=slot_key, pnl=-10, day_key="2024-01-01", capital_before=1000)
    gate.record_trade_result(i=2, slot_key=slot_key, pnl=20, day_key="2024-01-01", capital_before=1000)

    assert gate._slots[slot_key].consecutive_losses == 0
    assert not gate._slots[slot_key].paused


def test_pause_expire_apres_pause_bars():
    """La pause expire après pause_bars bougies."""
    gate = BacktestRiskGate(consec_loss_limit=1, pause_bars=5)
    slot_key = "trend@1h@BTC/USDC"

    # 1 perte → pause 5 bars
    gate.record_trade_result(i=10, slot_key=slot_key, pnl=-10, day_key="2024-01-01", capital_before=1000)
    assert gate._slots[slot_key].paused
    assert gate._slots[slot_key].pause_until_bar == 15

    # Avant la fin de pause → refus
    ok, _ = gate.can_slot_trade(i=14, slot_key=slot_key, day_key="2024-01-01",
                                 current_capital=990, peak_capital=1000)
    assert ok is False

    # Après la fin de pause → OK
    ok, _ = gate.can_slot_trade(i=15, slot_key=slot_key, day_key="2024-01-01",
                                 current_capital=990, peak_capital=1000)
    assert ok is True
    assert not gate._slots[slot_key].paused


def test_slot_daily_dd_limit_pause_slot():
    """DD journalier > seuil → pause slot."""
    gate = BacktestRiskGate(slot_daily_dd_limit=0.03)  # 3%
    slot_key = "trend@1h@BTC/USDC"

    # Perte de 4% du capital (1000) = 40 → DD journalier 4% > 3%
    gate.record_trade_result(i=0, slot_key=slot_key, pnl=-40, day_key="2024-01-01", capital_before=1000)

    assert gate._slots[slot_key].paused
    assert "DD journalier" in gate._slots[slot_key].pause_reason


def test_max_trades_per_day():
    """Limite trades/jour → refus après atteinte."""
    gate = BacktestRiskGate(max_trades_per_day=2)
    slot_key = "trend@1h@BTC/USDC"

    # 2 trades dans la journée
    gate.record_trade_result(i=0, slot_key=slot_key, pnl=10, day_key="2024-01-01", capital_before=1000)
    gate.record_trade_result(i=1, slot_key=slot_key, pnl=-5, day_key="2024-01-01", capital_before=1010)

    # 3e trade → refus
    ok, reason = gate.can_slot_trade(
        i=2, slot_key=slot_key, day_key="2024-01-01",
        current_capital=1005, peak_capital=1010,
    )
    assert ok is False
    assert "limite quotidienne" in reason.lower()


def test_max_trades_reset_nouveau_jour():
    """Nouveau jour → reset des compteurs journaliers."""
    gate = BacktestRiskGate(max_trades_per_day=2)
    slot_key = "trend@1h@BTC/USDC"

    # 2 trades le 2024-01-01
    gate.record_trade_result(i=0, slot_key=slot_key, pnl=10, day_key="2024-01-01", capital_before=1000)
    gate.record_trade_result(i=1, slot_key=slot_key, pnl=-5, day_key="2024-01-01", capital_before=1010)

    # Le 2024-01-02 → reset, trade OK
    ok, _ = gate.can_slot_trade(
        i=2, slot_key=slot_key, day_key="2024-01-02",
        current_capital=1005, peak_capital=1010,
    )
    assert ok is True
    assert gate._slots[slot_key].daily_trades == 0


def test_kill_switch_global_dd():
    """DD global > 20% → halt."""
    gate = BacktestRiskGate(global_dd_limit=0.20)
    slot_key = "trend@1h@BTC/USDC"

    # Capital 790 vs peak 1000 → DD 21% > 20%
    ok, reason = gate.can_slot_trade(
        i=0, slot_key=slot_key, day_key="2024-01-01",
        current_capital=790, peak_capital=1000,
    )
    assert ok is False
    assert gate.halted is True
    assert "DD global" in reason or "HALT" in reason


def test_volatility_brake():
    """ATR BTC > 5% → volatility_brake_active = True, factor = 0.5."""
    gate = BacktestRiskGate(volatility_threshold=0.05)

    gate.update_volatility(atr_pct=0.03)  # 3% < 5%
    assert not gate.volatility_brake_active
    assert gate.volatility_brake_factor == 1.0

    gate.update_volatility(atr_pct=0.06)  # 6% > 5%
    assert gate.volatility_brake_active
    assert gate.volatility_brake_factor == 0.5

    gate.update_volatility(atr_pct=0.04)  # 4% < 5%
    assert not gate.volatility_brake_active
    assert gate.volatility_brake_factor == 1.0


def test_reset():
    """reset() réinitialise tout."""
    gate = BacktestRiskGate(consec_loss_limit=1)
    slot_key = "trend@1h@BTC/USDC"

    gate.record_trade_result(i=0, slot_key=slot_key, pnl=-10, day_key="2024-01-01", capital_before=1000)
    gate.halted = True
    gate.halt_reason = "test"
    gate.update_volatility(atr_pct=0.10)

    gate.reset()

    assert gate.halted is False
    assert gate.halt_reason == ""
    assert not gate.volatility_brake_active
    assert gate.volatility_brake_factor == 1.0
    assert len(gate._slots) == 0


def test_to_diagnostics():
    """to_diagnostics() expose l'état pour audit."""
    gate = BacktestRiskGate(consec_loss_limit=1)
    slot_key = "trend@1h@BTC/USDC"

    gate.record_trade_result(i=0, slot_key=slot_key, pnl=-10, day_key="2024-01-01", capital_before=1000)
    gate.update_volatility(atr_pct=0.08)

    diag = gate.to_diagnostics()
    assert diag["halted"] is False
    assert diag["volatility_brake_active"] is True
    assert diag["n_slots_paused"] == 1
    assert slot_key in diag["slots"]
    assert diag["slots"][slot_key]["consecutive_losses"] == 1
    assert diag["slots"][slot_key]["paused"] is True


def test_halt_bloque_tous_slots():
    """Quand halted=True, tous les slots sont refusés."""
    gate = BacktestRiskGate()
    gate.halted = True
    gate.halt_reason = "DD global"

    ok, reason = gate.can_slot_trade(
        i=0, slot_key="any@1h@BTC/USDC", day_key="2024-01-01",
        current_capital=1000, peak_capital=1000,
    )
    assert ok is False
    assert "DD global" in reason or "HALT" in reason


def test_pause_daily_dd_est_levee_au_changement_de_jour():
    """La pause « DD journalier » ne dure QUE la journée.

    Régression : la levée de pause testait ``pause_reason.startswith("daily_dd")``
    alors que le motif rédigé commence par « DD journalier ». La condition
    n'était donc jamais vraie et le slot restait pausé jusqu'à la bougie
    ``i + 100000`` — c'est-à-dire toute la durée du backtest. Un seul mauvais
    jour tuait le slot définitivement, rendant le mode realistic_risk bien plus
    pessimiste que le RiskGate live qu'il est censé répliquer.
    """
    gate = BacktestRiskGate(slot_daily_dd_limit=0.03, consec_loss_limit=99)
    slot_key = "trend@1h@BTC/USDC"

    # DD journalier de 4% > 3% → pause
    gate.record_trade_result(i=10, slot_key=slot_key, pnl=-40,
                             day_key="2024-01-01", capital_before=1000)
    assert gate._slots[slot_key].paused
    assert gate._slots[slot_key].pause_kind == "daily_dd"

    # Même jour → toujours pausé
    ok, _ = gate.can_slot_trade(i=11, slot_key=slot_key, day_key="2024-01-01",
                                current_capital=960, peak_capital=1000)
    assert ok is False

    # Jour suivant → la pause est levée
    ok, reason = gate.can_slot_trade(i=12, slot_key=slot_key, day_key="2024-01-02",
                                     current_capital=960, peak_capital=1000)
    assert ok is True, f"le slot devait repartir à J+1, refusé : {reason}"
    assert gate._slots[slot_key].paused is False
    assert gate._slots[slot_key].pause_kind == ""


def test_pause_consec_loss_expire_apres_pause_bars_et_pas_avant():
    """La pause « pertes consécutives » expire au bout de `pause_bars` bougies.

    Contrairement à la pause DD journalier, elle ne doit PAS être levée par un
    simple changement de jour — les deux mécanismes ont des durées distinctes.
    """
    gate = BacktestRiskGate(consec_loss_limit=2, pause_bars=24,
                            slot_daily_dd_limit=1.0)
    slot_key = "trend@1h@BTC/USDC"

    gate.record_trade_result(i=0, slot_key=slot_key, pnl=-1,
                             day_key="2024-01-01", capital_before=1000)
    gate.record_trade_result(i=1, slot_key=slot_key, pnl=-1,
                             day_key="2024-01-01", capital_before=999)
    assert gate._slots[slot_key].pause_kind == "consec_loss"

    # Jour suivant mais pause_bars pas encore écoulées → toujours pausé
    ok, _ = gate.can_slot_trade(i=10, slot_key=slot_key, day_key="2024-01-02",
                                current_capital=998, peak_capital=1000)
    assert ok is False

    # Au-delà de pause_bars → repart
    ok, _ = gate.can_slot_trade(i=25, slot_key=slot_key, day_key="2024-01-02",
                                current_capital=998, peak_capital=1000)
    assert ok is True


# ── DD journalier GLOBAL (trading.daily_drawdown_limit) ──────────────────────

def test_dd_journalier_global_halte_puis_repart_le_lendemain():
    """Le breaker le plus susceptible de se déclencher — et il manquait.

    Le gate ne répliquait que le DD par slot et le DD depuis le pic. Le live a
    un TROISIÈME seuil, `trading.daily_drawdown_limit` (5 %), qui HALTe tout le
    bot sur la journée. Perdre 5 % dans la journée est bien plus fréquent que
    perdre 20 % depuis le pic : son absence rendait le mode realistic_risk
    optimiste là où il prétend être « close to live ».
    """
    gate = BacktestRiskGate(daily_dd_limit=0.05, global_dd_limit=0.99)
    slot = "trend@1h@BTC/USDC"

    # Première bougie du jour : fixe la référence de capital journalier.
    ok, _ = gate.can_slot_trade(i=0, slot_key=slot, day_key="2024-01-01",
                                current_capital=1000, peak_capital=1000)
    assert ok is True
    assert gate.daily_start_capital == 1000

    # -6 % dans la journée → HALT
    ok, reason = gate.can_slot_trade(i=5, slot_key=slot, day_key="2024-01-01",
                                     current_capital=940, peak_capital=1000)
    assert ok is False
    assert gate.halted is True
    assert gate.halt_kind == "daily_dd"
    assert "DD journalier" in reason

    # Toujours halté le même jour, même si le capital remonte un peu.
    ok, _ = gate.can_slot_trade(i=6, slot_key=slot, day_key="2024-01-01",
                                current_capital=955, peak_capital=1000)
    assert ok is False

    # Le lendemain : le HALT journalier est levé et la référence se recale.
    ok, _ = gate.can_slot_trade(i=30, slot_key=slot, day_key="2024-01-02",
                                current_capital=955, peak_capital=1000)
    assert ok is True
    assert gate.halted is False
    assert gate.halt_kind == ""
    assert gate.daily_start_capital == 955


def test_dd_global_halte_definitivement():
    """Le DD depuis le pic, lui, ne se lève PAS au changement de jour.

    En live il faut un `reset_halt()` de l'opérateur ; personne ne le fera
    pendant un backtest, et c'est fidèle : perdre 20 % depuis son pic arrête
    le bot pour de bon.
    """
    gate = BacktestRiskGate(daily_dd_limit=0.0, global_dd_limit=0.20)
    slot = "trend@1h@BTC/USDC"

    gate.can_slot_trade(i=0, slot_key=slot, day_key="2024-01-01",
                        current_capital=1000, peak_capital=1000)
    ok, _ = gate.can_slot_trade(i=5, slot_key=slot, day_key="2024-01-01",
                                current_capital=750, peak_capital=1000)
    assert ok is False
    assert gate.halt_kind == "global_dd"

    # Un mois plus tard : toujours halté.
    ok, _ = gate.can_slot_trade(i=5000, slot_key=slot, day_key="2024-02-01",
                                current_capital=750, peak_capital=1000)
    assert ok is False
    assert gate.halted is True


def test_pertes_consecutives_remises_a_zero_au_nouveau_jour():
    """Parité live : `_reset_slot_daily_pnl` remet `consecutive_losses` à 0.

    Sans ça, 2 pertes lundi + 1 perte mardi pausent le slot en backtest, alors
    que le live serait reparti de zéro mardi matin.
    """
    gate = BacktestRiskGate(consec_loss_limit=3, slot_daily_dd_limit=1.0)
    slot = "trend@1h@BTC/USDC"

    gate.record_trade_result(i=0, slot_key=slot, pnl=-1, day_key="2024-01-01", capital_before=1000)
    gate.record_trade_result(i=1, slot_key=slot, pnl=-1, day_key="2024-01-01", capital_before=999)
    assert gate._slots[slot].consecutive_losses == 2

    # Nouveau jour, nouvelle perte → compteur à 1, pas à 3.
    gate.record_trade_result(i=30, slot_key=slot, pnl=-1, day_key="2024-01-02", capital_before=998)
    assert gate._slots[slot].consecutive_losses == 1
    assert gate._slots[slot].paused is False


def test_volatility_brake_compte_les_bougies_ou_il_a_mordu():
    """Le frein doit être mesurable, sinon on ne sait pas s'il a joué."""
    gate = BacktestRiskGate(volatility_threshold=0.05)
    for atr in (0.01, 0.08, 0.09, 0.02):
        gate.update_volatility(atr)
    assert gate.volatility_brake_bars == 2
    assert gate.volatility_brake_active is False
    assert gate.volatility_brake_factor == 1.0
    assert gate.to_diagnostics()["volatility_brake_bars"] == 2
