# Audit — Sécurité et exploitation

> Périmètre : authentification et autorisation, gestion des secrets, exposition
> réseau, dépendances, conteneurisation, CI, journalisation.
>
> ⚠️ Aucun test d'intrusion n'a été effectué. Cet audit est une revue de code et
> de configuration.

---

## Tableau de bord

| # | Sévérité | Titre | Fichier | État au 18/08 |
|---|----------|-------|---------|---------------|
| S-01 | 🟠 Majeur | `/metrics` publie l'activité de trading sans authentification | `api/main.py:135-155` | ✅ résolu — `METRICS_TOKEN` ou `web.api_key` |
| S-02 | 🟡 Moyen | Rate limiting inopérant derrière un reverse proxy | `api/state.py:32` | ✅ résolu — même règle `TRUSTED_PROXIES` que l'auth |
| S-03 | 🟡 Moyen | Clé API acceptée en query string du WebSocket | `routes/ws.py` | ✅ résolu — `ALLOW_WS_QUERY_KEY=1` seulement |
| S-04 | 🟡 Moyen | Le cookie `api_key` ne porte `Secure` que sous condition | `frontend/src/app/api/[...path]/route.ts:103` | ✅ résolu — `x-forwarded-proto: https` |
| S-05 | 🟡 Moyen | `notifications.crash_include_log` peut exfiltrer positions et soldes | `config/ops.yaml`, `deploy/notify-crash.py` | ✅ atténué — caviardage pnl/size/capital |
| S-06 | 🟡 Moyen | `mypy` configuré très permissif et absent de la CI | `mypy.ini`, `.github/workflows/ci.yml` | ✅ job CI (continue-on-error) + 3.14 |
| S-07 | 🔵 Mineur | Le handler global renvoie le type d'exception | `api/middleware.py:49` | ✅ résolu — `correlation_id` |
| S-08 | 🔵 Mineur | 4 dépendances non épinglées sur 32 | `requirements.txt` | ✅ déjà `==` partout |
| S-09 | 🔵 Mineur | `git_commit()` lance un `subprocess` par process | `ml/model_registry.py:171` | ✅ cache + `GIT_COMMIT` |

> Détail : [`14-REVISION-2026-08-18.md`](14-REVISION-2026-08-18.md).

---

## S-01 🟠 `/metrics` sans authentification

`api/main.py:135-155`. L'endpoint est délibérément public, et la docstring
énonce le risque : « Il divulgue en revanche l'activité de trading (capital,
positions, PnL) — **à restreindre au réseau d'administration côté nginx** ».

Le problème est que cette restriction est **hors du dépôt** : elle dépend de la
configuration nginx du déploiement. Avec `web.host: 0.0.0.0` (défaut de
`config/ops.yaml`) et un port 8000 exposé, l'endpoint est accessible à quiconque
atteint la machine.

Ce que `/metrics` expose selon `core/metrics.py` : capital, PnL, nombre et
taille des positions, état des circuit breakers. Pour un attaquant, c'est de la
reconnaissance directe ; pour un concurrent, c'est le journal de trading.

Le motif invoqué (« un scrapeur Prometheus ne sait pas porter d'en-tête
`X-API-Key` sans configuration supplémentaire ») n'est pas exact : Prometheus
gère `authorization` et `bearer_token_file` dans sa configuration de scrape
depuis longtemps.

**Correction** :

```python
@app.get("/metrics")
def prometheus_metrics(request: Request):
    token = os.environ.get("METRICS_TOKEN") or (state.cfg or {}).get("web", {}).get("api_key", "")
    if token:
        supplied = (request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                    or request.headers.get("X-API-Key", ""))
        if not hmac.compare_digest(supplied, token):
            raise HTTPException(403, "…")
```

et documenter le `authorization` correspondant côté Prometheus.

Note : `/health` public est en revanche légitime — il ne renvoie que des
booléens (`db`, `exchange`, `trader`).

---

## S-02 🟡 Rate limiting inopérant derrière nginx

`api/state.py:32` : `Limiter(key_func=get_remote_address, ...)`.

`get_remote_address` lit l'IP du pair TCP et **n'honore pas**
`X-Forwarded-For` — c'est le bon choix de sécurité (non spoofable), cohérent
avec `helpers._extract_client_ip`.

Mais l'incohérence est que `_extract_client_ip` honore le header **si le pair
figure dans `TRUSTED_PROXIES`**, alors que le limiter ne le fait jamais. En
production derrière `deploy/nginx.conf`, tout arrive de `127.0.0.1` : les
300 req/min sont un seau **global**.

Deux effets, opposés et tous deux mauvais :

- **déni de service accidentel** : le frontend consomme ≈ 50 req/min par
  onglet (cf. U-03), donc six onglets saturent le seau pour tout le monde ;
- **absence de protection réelle** : un attaquant ne se distingue pas des
  utilisateurs légitimes, donc le limiter ne peut pas l'isoler.

**Correction** : `key_func` qui applique la même règle `TRUSTED_PROXIES` que
`_extract_client_ip`.

---

## S-03 🟡 Clé API en query string du WebSocket

`routes/ws.py`, `_check_ws_auth` : le cookie `api_key` est essayé en premier
(bon), avec repli sur `?api_key=xxx`.

Une URL de WebSocket portant la clé apparaît dans les journaux d'accès nginx
(`$request`), dans l'historique du navigateur si l'URL est ouverte directement,
et dans les traces de tout proxy intermédiaire. Le commentaire du dépôt
reconnaît l'enjeu (« jamais dans l'URL/les logs ») pour le chemin nominal, mais
le repli reste actif sans condition.

**Correction** : conditionner le repli à `ALLOW_WS_QUERY_KEY=1` et journaliser
un WARNING à chaque usage.

---

## S-04 🟡 Le cookie n'est `Secure` que sous condition

`frontend/src/app/api/[...path]/route.ts:103` :

```js
`api_key=${encodeURIComponent(API_KEY)}; Path=/; HttpOnly; SameSite=Lax${secure}`
```

`HttpOnly` et `SameSite=Lax` sont corrects — `Lax` bloque bien l'envoi du
cookie sur une requête POST cross-site, ce qui neutralise le CSRF sur les
routes d'écriture.

Reste deux points :

1. **`Secure` est conditionnel** (variable `secure`). Il faut vérifier que la
   condition couvre tous les déploiements HTTPS derrière un proxy qui termine
   TLS (le process Next voit alors du HTTP en interne). Le bon test est
   `x-forwarded-proto === 'https'`, pas le protocole local.
2. **`SameSite=Lax` laisse passer les navigations GET de premier niveau.** Il
   faut donc s'assurer qu'aucune route GET n'a d'effet de bord. Le recensement
   effectué (cf. [`05-BACKEND-API.md`](05-BACKEND-API.md)) montre que
   `start`/`stop`/`reset`/`apply` sont tous en POST : **c'est correct
   aujourd'hui**. C'est une propriété à préserver — une future route
   `GET /api/bot/restart` rouvrirait le CSRF.

---

## S-05 🟡 Journal de trading vers un tiers

`config/ops.yaml` :

```yaml
notifications:
  crash_include_log: false
```

Le réglage joint les dernières lignes de `logs/bot.log` à l'alerte de crash
envoyée via `deploy/notify-crash.py`. Le commentaire est explicite et juste :
« Telegram et CallMeBot sont des tiers, et le log porte symboles, tailles de
position et soldes. Passer à true est un choix explicite, pas un réglage de
confort. »

Le défaut `false` est le bon. Deux améliorations possibles :

- **caviarder** plutôt que tout ou rien : masquer montants et tailles, garder
  les traces d'exception ;
- pour CallMeBot en particulier, le message transite par un service tiers
  gratuit sans engagement de confidentialité — cela mérite un avertissement à
  l'activation.

---

## S-06 🟡 Typage statique permissif et hors CI

`mypy.ini` :

```ini
python_version = 3.12
ignore_missing_imports = True
follow_imports = silent
check_untyped_defs = False
disallow_untyped_defs = False
warn_unused_ignores = False
```

Toutes les options qui font travailler mypy sont désactivées. `follow_imports =
silent` supprime les erreurs des modules importés ; `check_untyped_defs = False`
ignore le corps de toute fonction non annotée — c'est-à-dire la majorité du
dépôt.

Et surtout : **mypy n'apparaît pas dans `.github/workflows/ci.yml`**. Les jobs
sont `lint` (ruff), `test` (pytest), `security` (pip-audit) et `frontend`
(lint + type-check + vitest). Le typage Python n'est vérifié nulle part.

Écart notable : le frontend, lui, fait tourner `npm run type-check` (`tsc
--noEmit`) en CI. Les deux côtés n'ont pas la même exigence.

Note secondaire : `mypy.ini` cible `python_version = 3.12` et `ruff.toml`
`target-version = "py312"`, alors que `requirements.txt` et le `Dockerfile`
exigent **Python 3.14**. Les linters analysent donc pour une version antérieure
à celle qui exécute.

**Correction progressive** : activer `check_untyped_defs = True` sur
`app/core/execution.py`, `risk_*.py` et `engine/opt_scoring.py` d'abord — les
modules monétaires, où une confusion de type a un coût direct — puis étendre.
Ajouter le job en CI en mode non bloquant d'abord.

---

## S-07 à S-09 (mineurs)

- **S-07** : `_global_exception_handler` (`api/middleware.py:49`) renvoie
  `{"detail": f"Erreur interne : {type(exc).__name__}"}`. Le nom de classe
  renseigne sur la pile interne. Le `correlation_id` est déjà disponible dans
  `request.state` — le renvoyer serait plus utile et moins bavard.
- **S-08** : `requirements.txt` compte 28 dépendances épinglées (`==`) et 4 en
  `>=`. `pip-audit` tourne en CI sur `requirements.txt` **et**
  `requirements-dev.txt` (bonne décision : « une CVE dans l'outillage de dev
  reste une CVE du dépôt »). Les 4 non épinglées échappent toutefois à la
  reproductibilité du build.
- **S-09** : `ml/model_registry.py:171` exécute `subprocess.run` pour lire le
  commit git courant, avec cache module-level. Dans un worker d'optimisation
  spawné, c'est un `fork/exec` par worker ; en conteneur sans `.git`, l'appel
  échoue à chaque process. Sans conséquence de sécurité, mais un
  `GIT_COMMIT` injecté au build (`ARG` Docker) serait plus propre.

---

## Ce qui est solide

La posture de sécurité est nettement au-dessus de ce qu'on trouve
habituellement dans ce type de projet.

- **Refus de démarrage bloquant** (`core/config.py`, OPS-02) :
  `web.host: 0.0.0.0` sans `web.api_key` **lève une `ValueError`** avec un
  message qui donne la commande pour générer une clé. L'override est en
  variable d'environnement (`ALLOW_INSECURE_WEB=1`) et **le YAML est
  explicitement ignoré** (SEC-003) — parce qu'un fichier de configuration
  éditable depuis l'UI ne doit pas pouvoir désarmer un garde-fou. Ce
  raisonnement est exactement le bon.
- **`hmac.compare_digest`** partout où une clé est comparée
  (`helpers.verify_api_key`, `main.get_status`, `ws._check_ws_auth`), avec
  bornage de longueur à 256 caractères avant comparaison.
- **Défense contre le spoofing de `X-Forwarded-For`**
  (`helpers._extract_client_ip`) : le header n'est honoré que si le pair TCP
  figure dans `TRUSTED_PROXIES`, **vide par défaut**. C'est la bonne valeur par
  défaut et le raisonnement est écrit.
- **Défense contre le log forging** (`middleware._incoming_correlation_id`) :
  identifiant client tronqué à 64 caractères et filtré sur `[A-Za-z0-9._-]`.
  Rare et pertinent.
- **Couverture d'authentification exhaustive** — vérifiée route par route :
  sur les **100 routes** déclarées dans `app/api/routes/`, **99 portent
  `dependencies=[Depends(verify_api_key)]`**. La centième est
  `@router.websocket("/ws")`, qui ne peut pas utiliser une dépendance FastAPI
  standard et applique son propre `_check_ws_auth` (même règle : cookie, repli
  query, `compare_digest`, filtre localhost sans clé). **Aucun endpoint métier
  n'est ouvert** — seuls `/health` et `/metrics`, montés hors router, le sont
  délibérément.
- **Clé API jamais dans le bundle client** : le proxy Next l'injecte côté
  serveur (`WEB_API_KEY`, sans préfixe `NEXT_PUBLIC_`), et le cookie posé est
  `HttpOnly`.
- **OpenAPI désactivé en production** (`ENV=prod` → `docs_url=None`,
  `redoc_url=None`, `openapi_url=None`).
- **Aucun `eval`, `exec`, `pickle.load`, `os.system`, `shell=True` ni
  `yaml.load` non sûr** dans tout `app/` (recherche exhaustive). Le registre ML
  affiche explicitement « aucun format pickle ». Le seul `subprocess` est un
  `git rev-parse` en lecture.
- **Aucune injection SQL possible** : tout passe par l'ORM SQLAlchemy ; les
  seuls f-strings SQL sont dans `_migrate_schema`, sur des noms de tables et de
  colonnes issus des métadonnées du code, jamais d'entrées utilisateur.
- **Docker durci** : multi-stage (compilateurs absents de l'image finale),
  **`USER bot`** non root, `HEALTHCHECK` sur `/health`, image de test taguée
  séparément pour ne pas écraser l'image de production, `.env` dans
  `.gitignore` (avec `.env.*`), aucun secret en dur dans le dépôt (vérifié).
- **CI complète** : ruff, pytest (**1 723 tests**, marqueur `slow` exclu),
  pip-audit sur les deux fichiers de dépendances, et côté frontend lint +
  `tsc --noEmit` + vitest. Le commentaire sur le `--ignore-vuln GHSA-xxxx`
  placeholder retiré (« il ne masquait rien et laissait croire qu'une exception
  était en place ») est révélateur d'une bonne discipline.
- **Alerte bloquante si `paper_mode: false` sans canal de notification**
  (`config.py`, OPS-04) : un HALT invisible en trading réel est effectivement
  la panne à éviter.
- **Kill-switch fichier + watchdog en process séparé**
  (`live/watchdog.py`) avec écriture atomique du heartbeat (`os.replace`).
