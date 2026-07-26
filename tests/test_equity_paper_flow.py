"""G2 — parcours complet d'un trade actions en paper, bout à bout.

C'est le test qui répond à la demande du sprint : « en attendant d'avoir un
live avec exécution des ordres, envoyer une notification de trade avec
symbole, direction, ouverture, SL, TP ». On monte un vrai ``LiveTrader`` sur
une venue actions data-only et on vérifie que :

  * aucun ordre n'atteint le broker ;
  * la notification émise porte bien symbole / sens / ouverture / SL / TP ;
  * la taille est un nombre entier de titres, jamais une fraction ;
  * les contraintes de la venue (short interdit, notionnel minimum, calendrier)
    sont appliquées ;
  * un trade crypto sur le même bot reste, lui, strictement inchangé.
"""
from datetime import datetime, timezone

import pytest

from app.core.bot_identity import build_pos_key, build_slot_key
from app.core.market_calendar import clear_cache as clear_calendar_cache
from app.live.live_trader import LiveTrader

EQUITY, CRYPTO = "AIR.PA", "BTC/USDC"
STRATEGY, TF = "trend_rider", "1h"


class RecordingExchange:
    """Exchange crypto mocké — enregistre tout ordre qui lui parvient."""

    def __init__(self, price=100.0):
        self.price = price
        self.orders = []

    def fetch_ticker(self, symbol):
        return {"last": self.price}

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        order = {"id": f"o{len(self.orders)}", "symbol": symbol, "side": side,
                 "amount": amount, "price": self.price, "average": self.price,
                 "filled": amount}
        self.orders.append(order)
        return order

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=None):
        return []

    def fetch_balance_detail(self):
        return {"free": 10_000.0, "used": 0.0, "total": 10_000.0, "borrowed": 0.0}

    def fetch_balance(self):
        return {"free": {"USDC": 10_000.0}, "total": {"USDC": 10_000.0}}


class CapturingNotifier:
    """Lit le flux mémoire du Notifier — rempli de façon SYNCHRONE par
    ``send()``, contrairement aux canaux réels qui passent par un worker
    asynchrone (et provoqueraient des tests instables). Les canaux sortants
    sont neutralisés pour qu'aucun appel réseau ne parte.

    Le flux stocke les messages démarqués (``_strip_markdown``) : les
    assertions portent donc sur le texte nu, sans backticks.
    """

    def __init__(self, notifier=None):
        self._notifier = notifier
        if notifier is not None:
            self.capture(notifier)

    def capture(self, notifier):
        self._notifier = notifier
        notifier._send_all = lambda *_a, **_kw: None
        notifier._email = lambda *_a, **_kw: None
        return notifier

    @property
    def messages(self):
        return [e["message"] for e in self._notifier.recent(limit=200)]

    def clear(self):
        self._notifier._feed.clear()

    def find(self, needle):
        return [m for m in self.messages if needle in m]


def _cfg(db_path, *, can_execute=False, min_notional=0.0, allow_short=False,
         calendar=""):
    return {
        "trading": {
            "capital": 10_000.0, "timeframes": [TF], "scan_interval": 60,
            "score_threshold": 0.5, "paper_mode": True, "max_positions": 5,
            "paper_slippage": 0.0, "taker_fee": 0.001,
        },
        "database": {"url": f"sqlite:///{db_path}"},
        "exchange": {"name": "okx", "margin": False},
        "venues": {
            "defs": {
                "spot": {"market_type": "spot"},
                "euronext-paper": {
                    "asset_class": "equity", "quote_currency": "EUR",
                    "fractional": False, "allow_short": allow_short,
                    "tick_size": 0.001, "min_notional": min_notional,
                    "calendar": calendar, "can_execute": can_execute,
                    "fee_pct": 0.001, "fee_min": 2.0,
                    "transaction_tax_pct": 0.004,
                },
            },
            "assign": {EQUITY: "euronext-paper"},
        },
        "strategies": {"enabled": [STRATEGY]},
        "strategy_params": {}, "optimizer_results": {},
        # L'allocateur dérive ses slots de scanner.symbols : les deux classes
        # d'actif doivent y figurer pour que le bot puisse les trader.
        "scanner": {"symbols": [CRYPTO, EQUITY]}, "risk": {},
        "notifications": {}, "optimizer": {"enabled": False},
        "forward_test": {"enabled": False}, "lifecycle": {"enabled": False},
        "capital_allocator": {},
    }


@pytest.fixture
def equity_trader(tmp_path):
    clear_calendar_cache()
    exchange = RecordingExchange(price=100.0)
    trader = LiveTrader(_cfg(str(tmp_path / "eq.db")), exchange)
    return trader, exchange, CapturingNotifier(trader.notif)


def _open(trader, symbol=EQUITY, side="long", price=100.0, stop_hint=95.0,
          tp_hint=115.0, score=0.8):
    signal = {"side": side, "score": score, "name": STRATEGY,
              "stop_hint": stop_hint, "reason": "cassure de range"}
    if tp_hint is not None:
        signal["tp_hint"] = tp_hint
    pos_key = build_pos_key(symbol, STRATEGY, TF)
    opened = trader._try_open_from_signal(
        pos_key=pos_key, symbol=symbol, strategy_name=STRATEGY, side=side,
        score=score, strat_threshold=0.5, tf=TF,
        slot_key=build_slot_key(STRATEGY, TF, symbol),
        signal_dict=signal, atr=1.0, price=price,
    )
    return opened, pos_key


# ── La notification de trade ───────────────────────────────────────────────

class TestTradeSignalNotification:
    def test_no_order_is_ever_sent_to_the_broker(self, equity_trader):
        trader, exchange, _ = equity_trader
        opened, _ = _open(trader)
        assert opened is True
        assert exchange.orders == [], \
            "une venue data-only ne doit transmettre aucun ordre"

    def test_notification_carries_symbol_direction_entry_sl_tp(self, equity_trader):
        trader, _, notes = equity_trader
        _open(trader, price=100.0, stop_hint=95.0, tp_hint=115.0)

        tickets = notes.find("TRADE À EXÉCUTER")
        assert len(tickets) == 1, "un ticket de trade, et un seul"
        ticket = tickets[0]
        assert EQUITY in ticket                       # symbole
        assert "ACHAT" in ticket and "LONG" in ticket  # direction
        assert "Ouverture : 100.0000" in ticket       # prix d'ouverture
        assert "Stop-loss : 95.0000" in ticket        # SL
        assert "Take-profit: 115.0000" in ticket      # TP
        assert "aucun ordre" in ticket.lower()

    def test_notification_reports_risk_reward(self, equity_trader):
        trader, _, notes = equity_trader
        _open(trader, price=100.0, stop_hint=95.0, tp_hint=115.0)
        # Risque 5, gain visé 15 → R:R 3.00
        assert "R:R 3.00" in notes.find("TRADE À EXÉCUTER")[0]

    def test_short_direction_reads_as_a_sell(self, tmp_path):
        clear_calendar_cache()
        trader = LiveTrader(_cfg(str(tmp_path / "s.db"), allow_short=True),
                            RecordingExchange(price=100.0))
        notes = CapturingNotifier(trader.notif)
        _open(trader, side="short", stop_hint=105.0, tp_hint=85.0)
        ticket = notes.find("TRADE À EXÉCUTER")[0]
        assert "VENTE" in ticket and "SHORT" in ticket

    def test_trailing_only_exit_is_stated_not_faked(self, equity_trader):
        trader, _, notes = equity_trader
        _open(trader, tp_hint=None)
        ticket = notes.find("TRADE À EXÉCUTER")[0]
        assert "trailing stop" in ticket

    def test_no_position_ouverte_message_that_would_imply_a_fill(self, equity_trader):
        trader, _, notes = equity_trader
        _open(trader)
        assert notes.find("Position ouverte") == [], \
            "annoncer « position ouverte » laisserait croire à un fill réel"

    def test_closing_also_asks_for_a_manual_action(self, equity_trader):
        trader, _, notes = equity_trader
        _, pos_key = _open(trader)
        notes.clear()
        trader._close_position(pos_key, 110.0, exit_reason="take_profit")
        tickets = notes.find("TRADE À SOLDER")
        assert len(tickets) == 1
        assert "VENDRE" in tickets[0] and EQUITY in tickets[0]

    def test_event_flag_can_silence_the_ticket(self, tmp_path):
        clear_calendar_cache()
        cfg = _cfg(str(tmp_path / "off.db"))
        cfg["notifications"] = {"events": {"on_trade_signal": False}}
        trader = LiveTrader(cfg, RecordingExchange(price=100.0))
        notes = CapturingNotifier(trader.notif)
        _open(trader)
        assert notes.find("TRADE À EXÉCUTER") == []


# ── Contraintes de la venue actions ────────────────────────────────────────

class TestVenueConstraints:
    def test_size_is_a_whole_number_of_shares(self, equity_trader):
        trader, _, _ = equity_trader
        _, pos_key = _open(trader)
        size = trader.open_positions[pos_key]["size"]
        assert size == int(size) and size >= 1, f"taille fractionnaire : {size}"

    def test_notional_matches_the_quantized_size(self, equity_trader):
        trader, _, _ = equity_trader
        _, pos_key = _open(trader)
        pos = trader.open_positions[pos_key]
        assert pos["notional"] == pytest.approx(pos["size"] * 100.0, rel=1e-6)

    def test_short_is_refused_when_the_venue_forbids_it(self, equity_trader):
        trader, _, _ = equity_trader
        opened, _ = _open(trader, side="short", stop_hint=105.0, tp_hint=85.0)
        assert opened is False
        assert trader.open_positions == {}
        assert any("venue" in e.get("reason", "") for e in trader.signal_log)

    def test_price_too_high_for_one_share_is_refused(self, tmp_path):
        """Capital insuffisant pour une seule action : refus explicite plutôt
        qu'un ordre de 0,4 titre impossible à passer."""
        clear_calendar_cache()
        trader = LiveTrader(_cfg(str(tmp_path / "hi.db")),
                            RecordingExchange(price=1_000_000.0))
        opened, _ = _open(trader, price=1_000_000.0, stop_hint=999_000.0,
                          tp_hint=1_010_000.0)
        assert opened is False

    def test_min_notional_is_enforced(self, tmp_path):
        clear_calendar_cache()
        trader = LiveTrader(_cfg(str(tmp_path / "mn.db"), min_notional=1e9),
                            RecordingExchange(price=100.0))
        opened, _ = _open(trader)
        assert opened is False

    def test_transaction_tax_is_charged_on_entry(self, equity_trader):
        trader, _, _ = equity_trader
        _, pos_key = _open(trader)
        pos = trader.open_positions[pos_key]
        notional = pos["size"] * pos["entry"]
        expected = max(notional * 0.001, 2.0) + notional * 0.004
        assert pos["fees"] == pytest.approx(expected, rel=1e-6)

    def test_venue_name_is_recorded_on_the_position(self, equity_trader):
        trader, _, _ = equity_trader
        _, pos_key = _open(trader)
        assert trader.open_positions[pos_key]["venue"] == "euronext-paper"


# ── Calendrier de marché ───────────────────────────────────────────────────

class TestMarketHoursGating:
    def _trader(self, tmp_path):
        clear_calendar_cache()
        return LiveTrader(_cfg(str(tmp_path / "cal.db"), calendar="XPAR"),
                          RecordingExchange(price=100.0))

    def test_equity_symbol_filtered_out_when_market_is_closed(self, tmp_path):
        trader = self._trader(tmp_path)
        night = datetime(2026, 7, 1, 3, 0, tzinfo=timezone.utc)
        assert trader._tradable_symbols([EQUITY, CRYPTO], now=night) == [CRYPTO]

    def test_both_kept_during_the_paris_session(self, tmp_path):
        trader = self._trader(tmp_path)
        midday = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        assert trader._tradable_symbols([EQUITY, CRYPTO], now=midday) == [EQUITY, CRYPTO]

    def test_crypto_is_never_filtered(self, tmp_path):
        trader = self._trader(tmp_path)
        for hour in (3, 10, 23):
            when = datetime(2026, 1, 1, hour, tzinfo=timezone.utc)   # jour férié
            assert trader._tradable_symbols([CRYPTO], now=when) == [CRYPTO]

    def test_market_open_survives_a_broken_calendar(self, tmp_path):
        """Un calendrier cassé ne doit pas geler le trading en silence."""
        trader = self._trader(tmp_path)

        class Boom:
            def is_open(self, _ts):
                raise RuntimeError("calendrier corrompu")
        trader._calendar_cache = {"euronext-paper": Boom()}
        assert trader._market_open(EQUITY) is True

    def test_session_end_closes_the_position(self, tmp_path):
        clear_calendar_cache()
        cfg = _cfg(str(tmp_path / "se.db"), calendar="XPAR")
        cfg["venues"]["defs"]["euronext-paper"]["close_at_session_end"] = True
        cfg["venues"]["defs"]["euronext-paper"]["close_before_close_min"] = 5.0
        trader = LiveTrader(cfg, RecordingExchange(price=100.0))
        _open(trader)
        assert trader.open_positions

        # 17:27 locale = 15:27 UTC, soit 3 min avant la clôture de 17:30.
        closing = datetime(2026, 7, 1, 15, 27, tzinfo=timezone.utc)
        assert trader._close_positions_at_session_end(now=closing) == 1
        assert trader.open_positions == {}

    def test_nothing_closed_in_the_middle_of_the_session(self, tmp_path):
        clear_calendar_cache()
        cfg = _cfg(str(tmp_path / "mid.db"), calendar="XPAR")
        cfg["venues"]["defs"]["euronext-paper"]["close_at_session_end"] = True
        trader = LiveTrader(cfg, RecordingExchange(price=100.0))
        _open(trader)
        midday = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
        assert trader._close_positions_at_session_end(now=midday) == 0
        assert trader.open_positions

    def test_crypto_position_is_never_closed_by_session_end(self, tmp_path):
        trader = self._trader(tmp_path)
        _open(trader, symbol=CRYPTO)
        for hour in (3, 15, 23):
            when = datetime(2026, 7, 1, hour, 27, tzinfo=timezone.utc)
            assert trader._close_positions_at_session_end(now=when) == 0
        assert trader.open_positions


# ── Non-régression crypto ──────────────────────────────────────────────────

class TestCryptoUnchanged:
    def test_crypto_trade_still_sends_a_real_order_and_the_usual_message(
            self, equity_trader):
        trader, exchange, notes = equity_trader
        opened, pos_key = _open(trader, symbol=CRYPTO)
        assert opened is True
        assert len(exchange.orders) == 1              # l'ordre part bien
        assert notes.find("Position ouverte")         # message habituel
        assert notes.find("TRADE À EXÉCUTER") == []   # pas de ticket manuel

    @pytest.mark.parametrize("symbol,expected", [(CRYPTO, 2.73), (EQUITY, 2.0)])
    def test_quantization_is_driven_by_the_venue_only(self, tmp_path, symbol, expected):
        """Même sizing fractionnaire (2,73 unités) des deux côtés : la crypto
        le garde intact, les actions l'arrondissent à 2. Preuve que la
        quantification suit la venue et n'est pas appliquée partout."""
        clear_calendar_cache()
        trader = LiveTrader(_cfg(str(tmp_path / f"q-{symbol[:3]}.db")),
                            RecordingExchange(price=100.0))
        trader.risk.compute_size = lambda *a, **kw: (2.73, 273.0)
        _, pos_key = _open(trader, symbol=symbol, tp_hint=None)
        assert trader.open_positions[pos_key]["size"] == expected

    def test_crypto_fees_are_purely_proportional(self, equity_trader):
        trader, _, _ = equity_trader
        _, pos_key = _open(trader, symbol=CRYPTO)
        pos = trader.open_positions[pos_key]
        assert pos["fees"] == pytest.approx(pos["size"] * pos["entry"] * 0.001)
