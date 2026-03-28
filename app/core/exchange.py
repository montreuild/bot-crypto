"""Client exchange CCXT avec retry exponentiel et reconnexion de session."""
import logging, time, functools
from typing import Callable, Any, Optional
import ccxt

logger = logging.getLogger(__name__)
MAX_RETRIES              = 4
BASE_DELAY               = 2.0
# Après N erreurs réseau consécutives → reset complet de la session TCP
RESET_AFTER_ERRORS       = 5
# Délai max entre deux tentatives de reconnexion (cap exponentiel à 5 min)
MAX_RECONNECT_DELAY      = 300.0


def with_retry(fn: Callable) -> Callable:
    """Retry avec backoff exponentiel pour les erreurs réseau/rate-limit."""
    @functools.wraps(fn)
    def wrapper(self_or_first, *args, **kwargs) -> Any:
        # Permet d'utiliser le décorateur sur méthodes ET fonctions libres
        instance = self_or_first if isinstance(self_or_first, RobustExchange) else None

        delay = BASE_DELAY
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = fn(self_or_first, *args, **kwargs)
                # Succès → reset compteur d'erreurs consécutives
                if instance is not None:
                    instance._consecutive_errors = 0
                return result
            except ccxt.RateLimitExceeded:
                wait = delay * (2 ** (attempt - 1))
                logger.warning(f"[Exchange] Rate limit — pause {wait:.0f}s ({attempt}/{MAX_RETRIES})")
                time.sleep(wait)
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                if instance is not None:
                    instance._consecutive_errors += 1
                    # Déclenchement du reset de session après RESET_AFTER_ERRORS erreurs
                    if instance._consecutive_errors >= RESET_AFTER_ERRORS:
                        logger.warning(
                            f"[Exchange] {instance._consecutive_errors} erreurs consécutives — "
                            f"reset de la session TCP…"
                        )
                        instance._reconnect()
                if attempt == MAX_RETRIES:
                    logger.error(f"[Exchange] Erreur réseau définitive : {e}")
                    raise
                wait = min(delay * (2 ** (attempt - 1)), MAX_RECONNECT_DELAY)
                logger.warning(f"[Exchange] Réseau KO — retry {attempt}/{MAX_RETRIES} dans {wait:.0f}s")
                time.sleep(wait)
            except ccxt.AuthenticationError:
                logger.error("[Exchange] ❌ Authentification échouée — vérifier clés API.")
                raise
            except ccxt.InsufficientFunds as e:
                logger.error(f"[Exchange] ❌ Fonds insuffisants : {e}")
                raise
            except ccxt.ExchangeError as e:
                logger.error(f"[Exchange] Erreur exchange : {e}")
                raise
        raise RuntimeError(f"Échec après {MAX_RETRIES} tentatives.")
    return wrapper


class RobustExchange:
    """Wrapper ccxt avec retry sur toutes les méthodes critiques + reconnexion de session."""
    def __init__(self, exchange: ccxt.Exchange, paper: bool = True,
                 margin: bool = False, margin_mode: str = "isolated"):
        self._ex                  = exchange
        self._ex_class            = exchange.__class__   # pour recréer lors du reset
        self._ex_config           = dict(getattr(exchange, "options", {}))  # options d'origine
        self.paper                = paper
        self.margin               = margin
        self.margin_mode          = margin_mode
        self._errors              = 0
        self._consecutive_errors  = 0   # compteur d'erreurs réseau consécutives
        self._last_reconnect_at   = 0.0 # timestamp du dernier reset

    def _reconnect(self):
        """Recrée une session TCP fraîche après N erreurs consécutives."""
        now = time.time()
        if now - self._last_reconnect_at < 60:  # anti-spam : max 1 reset/60s
            return
        self._last_reconnect_at = now
        try:
            opts = {"enableRateLimit": True, "timeout": 30000}
            if hasattr(self._ex, "apiKey") and self._ex.apiKey:
                opts["apiKey"] = self._ex.apiKey
                opts["secret"] = self._ex.secret
            original_opts = getattr(self._ex, "options", {})
            if "defaultType" in original_opts:
                opts["options"] = {"defaultType": original_opts["defaultType"]}
            new_ex = self._ex_class(opts)
            # Pas de set_sandbox_mode ici — voir commentaire dans create_exchange
            self._ex = new_ex
            self._consecutive_errors = 0
            logger.info("[Exchange] ✅ Session TCP réinitialisée avec succès.")
        except Exception as e:
            logger.error(f"[Exchange] ❌ Impossible de reconnecter : {e}")

    @with_retry
    def load_markets(self): return self._ex.load_markets()

    @with_retry
    def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
        return self._ex.fetch_ohlcv(symbol, timeframe, limit=limit, since=since)

    @with_retry
    def fetch_ticker(self, symbol): return self._ex.fetch_ticker(symbol)

    @with_retry
    def fetch_tickers(self, symbols=None): return self._ex.fetch_tickers(symbols)

    @with_retry
    def fetch_balance(self): return self._ex.fetch_balance()

    @with_retry
    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        if self.paper:
            logger.info(f"[PAPER{'·MARGIN' if self.margin else ''}] {side.upper()} {amount} {symbol} @ {price or 'market'}")
            return {"id": f"paper_{int(time.time())}", "status": "closed",
                    "symbol": symbol, "side": side, "amount": amount, "price": price or 0}
        p = dict(params or {})
        if self.margin:
            # Binance margin spot: emprunt et remboursement automatiques
            p.setdefault("sideEffectType", "AUTO_BORROW_REPAY")
            p.setdefault("isIsolated", str(self.margin_mode == "isolated").upper())
        return self._ex.create_order(symbol, order_type, side, amount, price, p)

    @with_retry
    def cancel_order(self, order_id, symbol):
        if self.paper: return {"id": order_id, "status": "canceled"}
        params = {"isIsolated": str(self.margin_mode == "isolated").upper()} if self.margin else {}
        return self._ex.cancel_order(order_id, symbol, params=params)

    @with_retry
    def fetch_margin_account(self) -> dict:
        """Retourne le compte margin (niveau de marge, emprunts, USDC libre)."""
        if self.paper:
            return {"marginLevel": 999.0, "totalNetAssetOfBtc": 0,
                    "userAssets": [], "totalCollateralValueInUSDT": 0}
        if self.margin_mode == "isolated":
            return self._ex.fetch_isolated_margin_account()
        return self._ex.fetch_cross_margin_account()

    @with_retry
    def fetch_margin_balance_usdc(self) -> float:
        """Retourne le solde USDC libre sur le compte margin."""
        if self.paper:
            return 0.0
        bal = self._ex.fetch_balance({"type": "margin"})
        return float(bal.get("USDC", {}).get("free", 0) or
                     bal.get("USDT", {}).get("free", 0))

    @with_retry
    def fetch_order(self, order_id, symbol):
        if self.paper: return {"id": order_id, "status": "closed"}
        return self._ex.fetch_order(order_id, symbol)

    # Accès direct aux attributs de l'exchange sous-jacent
    def __getattr__(self, name): return getattr(self._ex, name)


# Exchanges autorisés (whitelist sécurité — empêche l'accès arbitraire aux attributs ccxt)
_ALLOWED_EXCHANGES: frozenset = frozenset([
    "binance", "binanceus", "binanceusdm", "binancecoinm",
    "bybit", "okx", "kraken", "kucoin", "coinbase", "coinbasepro",
    "gateio", "huobi", "htx", "mexc", "bitfinex", "bitmex",
])


def create_exchange(cfg: dict) -> RobustExchange:
    name = cfg["exchange"]["name"].lower()
    if name not in _ALLOWED_EXCHANGES:
        raise ValueError(
            f"Exchange non autorisé : '{name}'. "
            f"Autorisés : {', '.join(sorted(_ALLOWED_EXCHANGES))}"
        )
    klass = getattr(ccxt, name, None)
    if klass is None:
        raise ValueError(f"Exchange non supporté par ccxt : {name}")
    paper = cfg["trading"].get("paper_mode", True)
    opts = {
        "enableRateLimit": True,
        "timeout":         30000,   # 30 s — évite les connexions pendantes indéfinies
    }
    if cfg["exchange"].get("futures"):
        opts["options"] = {"defaultType": "future"}
    elif cfg["exchange"].get("margin"):
        opts["options"] = {"defaultType": "margin"}
    api_key    = cfg["exchange"].get("api_key", "")
    api_secret = cfg["exchange"].get("api_secret", "")
    if api_key not in ("", "YOUR_KEY"):
        opts["apiKey"] = api_key
        opts["secret"] = api_secret
    ex = klass(opts)
    # set_sandbox_mode non activé : le paper trading est simulé localement par RobustExchange.
    margin      = cfg["exchange"].get("margin", False) or cfg["trading"].get("margin_mode") is not None
    margin_mode = cfg["trading"].get("margin_mode", "isolated")  # "isolated" | "cross"
    return RobustExchange(ex, paper=paper, margin=margin, margin_mode=margin_mode)
