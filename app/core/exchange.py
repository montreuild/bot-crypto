"""Client exchange CCXT avec retry exponentiel et reconnexion de session."""
import functools
import logging
import time
import uuid
from typing import Any, Callable, Optional

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


def _safe_float(x, default: Optional[float] = None) -> Optional[float]:
    """Float robuste : None / "" / valeur non numérique → ``default``."""
    if x is None or x == "":
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class RobustExchange:
    """Wrapper ccxt avec retry sur toutes les méthodes critiques + reconnexion de session."""
    def __init__(self, exchange: ccxt.Exchange, paper: bool = True,
                 margin: bool = False, margin_mode: str = "isolated"):
        self._ex                  = exchange
        self._ex_class            = exchange.__class__   # pour recréer lors du reset
        self._ex_config           = dict(getattr(exchange, "options", {}))  # options d'origine
        # Identifiant ccxt de l'exchange (ex. "okx") — pilote les quirks par
        # exchange (params margin, format du clientOrderId, etc.).
        self._name                = (getattr(exchange, "id", "") or "").lower()
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
                # OKX (et quelques autres) exigent une passphrase en 3e credential :
                # sans elle, la session reconstruite perdrait l'authentification.
                if getattr(self._ex, "password", None):
                    opts["password"] = self._ex.password
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

    # ── Ordres : création idempotente ───────────────────────────────────────
    #
    # Un retry aveugle après timeout réseau peut DUPLIQUER un ordre market si
    # l'exchange avait en fait reçu la requête (double position, stops faux).
    # On attache donc un identifiant client déterministe (clOrdId OKX) à chaque appel et,
    # avant chaque nouvelle tentative après une erreur réseau, on vérifie si
    # l'ordre existe déjà côté exchange — auquel cas on le retourne au lieu
    # d'en créer un second.

    @staticmethod
    def _gen_client_order_id() -> str:
        # Alphanumérique pur, 27 caractères → conforme au clOrdId OKX
        # (lettres + chiffres, 32 max, pas de tiret) : un préfixe non
        # alphanumérique ou une longueur excessive serait rejeté.
        return f"bot{uuid.uuid4().hex[:24]}"

    def _client_id_field(self) -> str:
        """Nom du paramètre clientOrderId à la *création* d'ordre, par exchange."""
        if self._name == "okx":
            return "clOrdId"
        return "clientOrderId"   # paramètre unifié ccxt (traduit par exchange)

    def _margin_params(self) -> dict:
        """Paramètres de marge propres à l'exchange (vide si spot ou non margin).

        OKX (compte unifié) : ``tdMode`` = isolated|cross — l'auto-borrow y est
        géré au niveau du compte, pas par ordre.
        """
        if not self.margin:
            return {}
        if self._name == "okx":
            return {"tdMode": "isolated" if self.margin_mode == "isolated" else "cross"}
        # Exchange margin générique : laisse ccxt router via marginMode.
        return {"marginMode": self.margin_mode}

    def _fetch_order_by_client_id(self, client_id: str, symbol: str) -> Optional[dict]:
        """Recherche un ordre par clientOrderId. None si introuvable/erreur."""
        try:
            if self._name == "okx":
                params = {"clOrdId": client_id}
            else:
                params = {"clientOrderId": client_id}
            params.update(self._margin_params())
            order = self._ex.fetch_order(None, symbol, params)
            return order or None
        except Exception:
            return None

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        if self.paper:
            logger.info(f"[PAPER{'·MARGIN' if self.margin else ''}] {side.upper()} {amount} "
                       f"{symbol} @ {price or 'market'}")
            return {"id": f"paper_{int(time.time())}", "status": "closed",
                    "symbol": symbol, "side": side, "amount": amount, "price": price or 0}
        p = dict(params or {})
        for k, v in self._margin_params().items():
            p.setdefault(k, v)
        client_id = p.setdefault(self._client_id_field(), self._gen_client_order_id())

        delay = BASE_DELAY
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = self._ex.create_order(symbol, order_type, side, amount, price, p)
                self._consecutive_errors = 0
                return result
            except ccxt.RateLimitExceeded:
                wait = delay * (2 ** (attempt - 1))
                logger.warning(f"[Exchange] Rate limit — pause {wait:.0f}s ({attempt}/{MAX_RETRIES})")
                time.sleep(wait)
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                self._consecutive_errors += 1
                if self._consecutive_errors >= RESET_AFTER_ERRORS:
                    logger.warning(
                        f"[Exchange] {self._consecutive_errors} erreurs consécutives — "
                        f"reset de la session TCP…"
                    )
                    self._reconnect()
                # L'ordre a peut-être été reçu malgré le timeout : vérification
                # par clientOrderId avant tout retry (idempotence).
                existing = self._fetch_order_by_client_id(client_id, symbol)
                if existing is not None:
                    logger.warning(
                        f"[Exchange] Timeout sur create_order {symbol} mais l'ordre "
                        f"{existing.get('id')} (clientOrderId={client_id}) existe déjà "
                        f"— réutilisé, pas de doublon."
                    )
                    self._consecutive_errors = 0
                    return existing
                if attempt == MAX_RETRIES:
                    logger.error(f"[Exchange] Erreur réseau définitive (create_order) : {e}")
                    raise
                wait = min(delay * (2 ** (attempt - 1)), MAX_RECONNECT_DELAY)
                logger.warning(
                    f"[Exchange] Réseau KO sur create_order — retry {attempt}/{MAX_RETRIES} "
                    f"dans {wait:.0f}s (clientOrderId={client_id})"
                )
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

    @with_retry
    def cancel_order(self, order_id, symbol):
        if self.paper:
            return {"id": order_id, "status": "canceled"}
        # OKX annule par ordId sans paramètre de marge supplémentaire.
        return self._ex.cancel_order(order_id, symbol)

    @with_retry
    def fetch_margin_account(self) -> dict:
        """Retourne le compte margin (niveau de marge, emprunts, USDC libre).

        Sur le compte unifié OKX, on dérive un niveau de marge **non ambigu**
        depuis ``fetch_balance().info.data[0]`` :

            marginLevel = adjEq / mmr

        où ``adjEq`` = équité ajustée (USD) et ``mmr`` = maintenance margin
        requirement (USD). C'est un ratio **décimal** (liquidation ≈ 1.0, plus
        c'est haut plus c'est sûr), calculé nous-mêmes pour éviter l'ambiguïté
        d'échelle du champ brut ``mgnRatio`` (fraction vs pourcentage selon le
        mode de compte). Sans positions margin (``mmr`` = 0) → pas de risque
        (999). Fallback sur ``mgnRatio`` brut puis 999. Cf. docs/MIGRATION_OKX.md.
        """
        if self.paper:
            return {"marginLevel": 999.0, "totalNetAssetOfBtc": 0,
                    "userAssets": [], "totalCollateralValueInUSDT": 0}
        if self._name == "okx":
            bal  = self._ex.fetch_balance()
            acct = (((bal.get("info") or {}).get("data") or [{}]) or [{}])[0]
            adj_eq = _safe_float(acct.get("adjEq"))
            mmr    = _safe_float(acct.get("mmr"))
            if mmr is not None and mmr > 0 and adj_eq is not None:
                ml = adj_eq / mmr                       # ratio décimal, liquidation ≈ 1.0
            else:
                # Pas de mmr (aucune position margin) → sûr ; sinon mgnRatio brut.
                ml = _safe_float(acct.get("mgnRatio"), default=999.0)
            return {"marginLevel": round(ml, 4), "info": bal.get("info", {})}
        return {"marginLevel": 999.0}

    @with_retry
    def fetch_margin_balance_usdc(self) -> float:
        """Retourne le solde USDC (ou USDT) libre sur le compte margin."""
        if self.paper:
            return 0.0
        # OKX = compte unifié (pas de wallet ``type`` séparé).
        bal = self._ex.fetch_balance()
        return float(bal.get("USDC", {}).get("free", 0) or
                     bal.get("USDT", {}).get("free", 0))

    @with_retry
    def fetch_balance_detail(self) -> dict:
        """Retourne le détail du solde USDC/USDT : free, used, total, borrowed.

        En mode spot  : free = disponible, used = engagé en ordres ouverts.
        En mode margin: idem + borrowed = montant emprunté sur le compte margin.
        """
        if self.paper:
            return {"free": 0.0, "used": 0.0, "total": 0.0, "borrowed": 0.0}
        # OKX = compte unifié (pas de wallet ``type`` séparé).
        bal    = self._ex.fetch_balance()
        usdc   = bal["USDC"] if "USDC" in bal else bal.get("USDT") or {}
        free   = float(usdc.get("free",  0) or 0)
        used   = float(usdc.get("used",  0) or 0)
        total  = float(usdc.get("total", 0) or 0)

        borrowed = 0.0
        if self.margin and self._name == "okx":
            try:
                # OKX : la dette par devise est le champ ``liab`` des details
                # de fetch_balance().info.data[0].
                data = ((bal.get("info") or {}).get("data") or [{}])
                for d in (data[0].get("details") or []):
                    if d.get("ccy") in ("USDC", "USDT"):
                        borrowed += float(d.get("liab", 0) or 0)
            except Exception as e:
                logger.debug(f"[Balance] Emprunts non récupérés : {e}")

        return {
            "free":     round(free, 4),
            "used":     round(used, 4),
            "total":    round(total, 4),
            "borrowed": round(borrowed, 4),
        }

    @with_retry
    def fetch_order(self, order_id, symbol):
        if self.paper:
            return {"id": order_id, "status": "closed"}
        return self._ex.fetch_order(order_id, symbol)

    # Accès direct aux attributs de l'exchange sous-jacent
    def __getattr__(self, name): return getattr(self._ex, name)


# Exchanges autorisés (whitelist sécurité — empêche l'accès arbitraire aux attributs ccxt)
# OKX est l'exchange cible ; les autres restent ouverts via le routage ccxt générique.
_ALLOWED_EXCHANGES: frozenset = frozenset([
    "okx", "bybit", "kraken", "kucoin", "coinbase", "coinbasepro",
    "gateio", "huobi", "htx", "mexc", "bitfinex", "bitmex",
])


def create_exchange(cfg: dict):
    """Construit l'accès marché du bot.

    Retourne un ``RobustExchange`` (ccxt) dans le cas nominal. Si — et
    seulement si — une venue déclare un ``data_provider`` alternatif (G2 :
    actions via yfinance), l'objet est enveloppé dans un ``ProviderRouter`` qui
    expose la **même** interface et aiguille chaque symbole vers son provider.
    Aucun appelant n'a donc à connaître la classe d'actif qu'il manipule.
    """
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
        # OKX (et Kucoin, Coinbase Pro…) exigent une passphrase API en 3e
        # credential. Acceptée sous api_password / api_passphrase / password.
        passphrase = (cfg["exchange"].get("api_password")
                      or cfg["exchange"].get("api_passphrase")
                      or cfg["exchange"].get("password", ""))
        if passphrase and passphrase not in ("YOUR_KEY",):
            opts["password"] = passphrase
        elif name in ("okx", "kucoin", "coinbase", "coinbasepro"):
            logger.warning(
                f"⚠ [Exchange] {name} exige une passphrase API (exchange.api_password) "
                f"— absente : les appels authentifiés (live) échoueront."
            )
    ex = klass(opts)
    # set_sandbox_mode non activé : le paper trading est simulé localement par RobustExchange.
    margin      = cfg["exchange"].get("margin", False) or cfg["trading"].get("margin_mode") is not None
    margin_mode = cfg["trading"].get("margin_mode", "isolated")  # "isolated" | "cross"
    robust = RobustExchange(ex, paper=paper, margin=margin, margin_mode=margin_mode)

    # Import local : le routeur n'est chargé que si une venue le réclame, et
    # `provider_router` importe `bot_identity` — un import au niveau module
    # créerait un cycle avec les modules qui importent `exchange`.
    from app.core.provider_router import build_market_provider
    return build_market_provider(cfg, robust)
