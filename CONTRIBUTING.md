# 🤝 Guide de contribution

Comment contribuer au Crypto Bot V11 et maintenir la qualité du code.

---

## 📋 Code de conduite

- ✅ Respect, inclusivité, collaboration
- ✅ Feedback constructif
- ❌ Pas de spam, harrasement, discrimination

---

## 🚀 Pour démarrer

### 1. Forker et cloner

```bash
git clone https://github.com/YOUR_GITHUB/bot-crypto.git
cd bot-crypto
git remote add upstream https://github.com/montreuild/bot-crypto.git
```

### 2. Créer une branche

```bash
git checkout -b feature/my-feature
# OU
git checkout -b bugfix/issue-123
```

**Convention de nom** :
- `feature/new-strategy` — Nouvelle stratégie
- `bugfix/null-pointer-exception` — Correction bug
- `perf/cache-strategies` — Optimisation
- `docs/add-api-reference` — Documentation
- `refactor/split-engine-module` — Refactoring

### 3. Développer

```bash
# Code dans ton branche
git add .
git commit -m "feat: ajouter stratégie RSI reversal"
```

**Format commit** (Conventional Commits) :
```
feat: ajouter support multi-timeframe optimizer
fix: corriger NaN dans equity curve
perf: cacher les stratégies découvertes (TTL 300s)
docs: ajouter section API key dans README
test: ajouter tests unitaires Backtester
refactor: extraire logique sérialisation JSON
```

### 4. Soumettre une PR

```bash
git push origin feature/my-feature
# → Créer PR sur GitHub
```

**Template PR** :

```markdown
## Description

Explique brièvement le changement.

## Type de changement

- [ ] 🐛 Bug fix (changement non-breaking)
- [ ] ✨ Feature (changement non-breaking)
- [ ] 🔴 Breaking change
- [ ] 📚 Docs update

## Checklist

- [ ] Code suivit PEP 8
- [ ] Tests ajoutés / passent
- [ ] Doc mise à jour
- [ ] Messages commit clairs

## Tests effectués

```bash
pytest tests/
```

Décris les tests et résultats.
```

---

## 📐 Conventions de code

### Style

- **Python** : PEP 8, line length 120 chars
- **Type hints** : Requis pour nouvelles fonctions
- **Docstrings** : Google style

```python
def list_trades(session: Session, limit: int = 100, offset: int = 0) -> list[Trade]:
    """
    Retourne une liste de trades.
    
    Args:
        session: SQLAlchemy Session
        limit: Nombre max de trades (défaut: 100)
        offset: Décalage pour pagination (défaut: 0)
        
    Returns:
        Liste de Trade ORM objects
    """
    return session.query(Trade).offset(offset).limit(limit).all()
```

### Logging

```python
import logging
logger = logging.getLogger(__name__)

logger.debug("Valeur debug", extra={"value": 123})
logger.info("[Module] Action", extra={"action": "start"})
logger.warning("[Module] Attention", extra={"risk": "high"})
logger.error("[Module] Erreur", exc_info=True)
```

### Error Handling

```python
# ✅ BON
try:
    result = risky_operation()
except SpecificError as e:
    logger.error(f"[Operation] Erreur spécifique: {e}")
    raise HTTPException(400, "Opération échouée")

# ❌ MAUVAIS
try:
    result = risky_operation()
except:
    pass  # Silence les erreurs !
```

### Security

```python
# ❌ DANGEREUX
exec(user_input)
import importlib; importlib.import_module(user_input)

# ✅ SÛR
ALLOWED_MODULES = {"strategy_1", "strategy_2"}
if user_input not in ALLOWED_MODULES:
    raise ValueError("Module non autorisé")
mod = importlib.import_module(f"app.strategies.{user_input}")
```

---

## 🧪 Tests

### Exécuter les tests

```bash
pytest tests/ -v
pytest tests/test_backtest.py::test_simple_strategy
pytest tests/ --cov=app  # Coverage report
```

### Écrire des tests

```python
# tests/test_strategies.py
import pytest
from app.strategies.pullback_trend import Strategy
from app.engine.engine import Engine

def test_pullback_trend_signal():
    """Test que la stratégie génère un signal sur data simple."""
    strategy = Strategy()
    engine = Engine()
    engine.register(strategy)
    
    # Données simulées
    ohlcv = {
        "time": 1234567890000,
        "close": 50000,
        "high": 50500,
        "low": 49500,
        "volume": 1000
    }
    
    signal = engine.signal(ohlcv)
    assert signal is not None
    assert signal.side in ["LONG", "SHORT"]
```

**Frameworks** :
- pytest — Tests unitaires
- pytest-cov — Coverage
- httpx — Tests API

### Un correctif = un test qui échoue d'abord

Pour tout garde-fou ou correction de comportement, **écrire d'abord le test qui
échoue sur le code actuel**, puis corriger. La revue du 20 août a trouvé six
garde-fous écrits mais reliés à aucune décision — refus de drawdown non
transmis à la route d'application, verdict de qualité ML qui n'alimentait qu'un
log, critères de comparaison dont ni les valeurs ni le baseline n'étaient
fournis. Tous exprimaient la bonne intention ; aucun n'avait de test qui
échoue avant le correctif, et aucun n'a jamais rien bloqué.

---

## ✅ Ce que la CI vérifie

Les huit jobs bloquent la PR. Les reproduire en local :

```bash
ruff check .
python -m mypy app/core app/engine app/live app/ml app/api
pytest tests/ -q -m "not slow" --cov=app --cov-fail-under=64
```

```bash
cd frontend && npm run lint && npm run type-check && npm run test:coverage && npm run build
```

| Job | Portée |
|---|---|
| `lint` | `ruff check .` — tout le dépôt, version épinglée |
| `mypy` | `app/core`, `app/engine`, `app/live`, `app/ml`, `app/api` ; `check_untyped_defs` actif sur ces paquets (`mypy.ini`) |
| `test` | pytest hors `slow`, **plancher de couverture 64 %** |
| `frontend` | eslint, `tsc --noEmit`, vitest, build |
| `e2e` | Playwright — chargement des pages |
| `a11y` | axe sur 20 pages |
| `visual` | instantanés de rendu |
| `security` | `pip-audit` |

Les tests marqués `slow` tournent dans un workflow séparé
(`.github/workflows/slow.yml`), pas sur les PR.

**Périmètre mypy** — il s'élargit par lots. `app/strategies` n'est pas
encore couvert : y ajouter du code non typé ne bloque pas aujourd'hui,
mais le lot suivant le rattrapera.

---

## 📚 Documentation

### Ajouter de la doc

1. **Docstrings code**
   ```python
   def analyze(self, ohlcv) -> Signal:
       """Analyze OHLCV candle and return trading signal."""
       ...
   ````

2. **Fichiers markdown** (``docs/*.md``)
   ```markdown
   # Title
   ## Subtitle
   ### Code
   ```

3. **Commentaires inline**
   ```python
   # Valider en whitelist pour éviter injection
   if strategy not in ALLOWED_STRATS:
       raise ValueError(...)
   ```

### Mettre à jour README

Si tu changes un argument CLI, une route API ou ajoutes une feature :

```bash
# Éditer README.md
git add README.md
git commit -m "docs: ajouter nouvelle option --custom"
```

---

## 🔄 Processus de review

1. **Soumettre PR** → Checklist GitHub
2. **Code review** (maintainers) → Commentaires
3. **Itérer** → Apporter corrections
4. **Approve** → 2 reviewers minimum
5. **Merge** → Rebase + squash commits

### Checklist reviewer

- [ ] Code PEP 8 ?
- [ ] Type hints complets ?
- [ ] Tests couvrent les cas limites ?
- [ ] Pas de secrets en code ?
- [ ] Logs suffisants ?
- [ ] Perf impact ?
- [ ] Doc mise à jour ?

---

## 🚀 Projets bienvenu

### Stratégies

Créer une nouvelle stratégie ? Super !

```python
# app/strategies/my_strategy.py
from app.engine.engine import BaseStrategy

class Strategy(BaseStrategy):
    def __init__(self):
        self.name = "my_strategy"
        self.params = {
            "fast_period": 10,
            "slow_period": 30,
        }
    
    def analyze(self, ohlcv) -> Signal:
        # Ton logique ici
        return Signal(side="LONG", size=1.0, stop=49000, reason="...")
```

Puis enregistrer dans `config.yaml` :
```yaml
strategies:
  enabled:
    - my_strategy
```

### Améliorations

- Optimiseur : Ajouter support Hyperopt, Optuna
- API : Rate limiting, WebSocket
- ML : LSTM, Transformer
- Backtest : GPU acceleration (Numba)
- UI : Charts interactifs, animations

---

## 🐛 Signaler un bug

### Template issue

```markdown
## Description

Brief description.

## Étapes pour reproduire

1. Faire X
2. Faire Y
3. Observer Z

## Comportement actuel

...

## Comportement attendu

...

## Environment

- OS: Ubuntu 24.04
- Python: 3.12.0
- Bot Version: 11.0.0
```

---

## 📊 Performance

### Avant d'optimiser

1. **Profiler** (voir où le temps est dépensé)
   ```bash
   python -m cProfile -s cumtime cli.py --backtest BTC/USDC
   ````

2. **Mesurer avant/après**
   ```python
   import time
t0 = time.time()
result = expensive_operation()
print(f"Temps : {time.time() - t0:.2f}s")
```

3. **Benchmark**
   ```bash
   pytest tests/test_backtest.py --benchmark
   ```

### Areas d'optimisation prioritaires

- ⚡ Backtest (Polars, numpy hot loops)
- ⚡ DB queries (index, connection pool)
- ⚡ API responses (caching, pagination)

---

## 🔐 Security Guidelines

1. **Input Validation**
   ```python
   if not strategy in ALLOWED_STRATEGIES:
       raise ValueError("Strategy not allowed")
   ```

2. **Secrets**
   ```python
   # ❌ NE PAS faire
   api_key = "hard-coded-key"
   
   # ✅ FAIRE
   api_key = os.environ.get("OKX_API_KEY")
   ```

3. **SQL Injection Prevention**
   ```python
   # ❌ NE PAS faire
   session.query(f"SELECT * FROM trades WHERE symbol = '{symbol}'")
   
   # ✅ FAIRE
   session.query(Trade).filter(Trade.symbol == symbol).all()
   ```

---

## 🎯 Roadmap contribueurs

### Facile (Bon premier PR)

- [ ] Ajouter tests manquants
- [ ] Fixer typos doc
- [ ] Améliorer messages d'erreur

### Moyen

- [ ] Nouvelles stratégies
- [ ] Améliorations UX/UI
- [ ] Optimisations perf

### Difficile

- [ ] Support multi-account
- [ ] Backtester distribué
- [ ] ML models (LSTM, etc)

---

## 📞 Besoin d'aide ?

- 💬 GitHub Discussions : Questions générales
- 🐛 GitHub Issues : Bugs signalés
- 📧 Email : contact@example.com

---

**Merci de contribuer au Crypto Bot V11 !** 🙌

Tes contributions rendent ce projet meilleur chaque jour.