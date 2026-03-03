"""
Client exchange CCXT avec retry exponentiel, fallback et monitoring.
"""
import logging, time, functools
from typing import Callable, Any, Optional
import ccxt

logger = logging.getLogger(__name__)
MAX_RETRIES = 4
BASE_DELAY  = 2.0


def with_retry(fn: Callable) -> Callable:
    """Retry avec backoff exponentiel pour les erreurs réseau/rate-limit."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> Any:
        delay = BASE_DELAY
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except ccxt.RateLimitExceeded:
                wait = delay * (2 ** (attempt - 1))
                logger.warning(f"[Exchange] Rate limit — pause {wait:.0f}s ({attempt}/{MAX_RETRIES})")
                time.sleep(wait)
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                if attempt == MAX_RETRIES:
                    logger.error(f"[Exchange] Erreur réseau définitive : {e}")
                    raise
                wait = delay * (2 ** (attempt - 1))
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
    """Wrapper ccxt avec retry sur toutes les méthodes critiques."""
    def __init__(self, exchange: ccxt.Exchange, paper: bool = True):
        self._ex     = exchange
        self.paper   = paper
        self._errors = 0

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
            logger.info(f"[PAPER] {side.upper()} {amount} {symbol} @ {price or 'market'}")
            return {"id": f"paper_{int(time.time())}", "status": "closed",
                    "symbol": symbol, "side": side, "amount": amount, "price": price or 0}
        return self._ex.create_order(symbol, order_type, side, amount, price, params or {})

    @with_retry
    def cancel_order(self, order_id, symbol):
        if self.paper: return {"id": order_id, "status": "canceled"}
        return self._ex.cancel_order(order_id, symbol)

    @with_retry
    def fetch_order(self, order_id, symbol):
        if self.paper: return {"id": order_id, "status": "closed"}
        return self._ex.fetch_order(order_id, symbol)

    # Accès direct aux attributs de l'exchange sous-jacent
    def __getattr__(self, name): return getattr(self._ex, name)


def create_exchange(cfg: dict) -> RobustExchange:
    name = cfg["exchange"]["name"].lower()
    klass = getattr(ccxt, name, None)
    if klass is None:
        raise ValueError(f"Exchange non supporté par ccxt : {name}")
    paper = cfg["trading"].get("paper_mode", True)
    opts = {"enableRateLimit": True}
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
    if paper and hasattr(ex, "set_sandbox_mode"):
        try: ex.set_sandbox_mode(True)
        except Exception: pass
    return RobustExchange(ex, paper=paper)
