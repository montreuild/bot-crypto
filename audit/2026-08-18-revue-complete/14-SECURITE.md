# 14 — Sécurité

Périmètre : `app/api/helpers.py`, `app/api/middleware.py`, `app/api/main.py`,
`app/api/routes/ws.py`, `app/core/config.py`, `app/core/exchange.py`,
`app/ml/backend/persistence.py`, `frontend/src/app/api/[...path]/route.ts`, `.gitlab-ci.yml`.

**Jugement d'ensemble.** C'est le domaine le mieux traité du dépôt, et de loin. Les
mécanismes en place ne sont pas des cases cochées : ils sont corrects dans le détail
— comparaison en temps constant, repli fermé plutôt qu'ouvert, `X-Forwarded-For` derrière
liste blanche de proxys, en-tête de corrélation assaini contre l'injection de log, cookie
`HttpOnly; SameSite=Lax; Secure`, suppression complète de `pickle` du chemin de
chargement des modèles, `pip-audit` bloquant en CI. Un seul constat sort du bruit, et
c'est une fuite d'information, pas une faille exploitable.

---

## SEC-01 — Huit endpoints renvoient le message d'exception interne au client

**Sévérité P2 · CONFIRMÉ (recensement)**

```
app/api/routes/ml.py:454, 503, 525, 569, 654   HTTPException(500, f"Erreur interne : {e}")
app/api/routes/backtest.py:79                  HTTPException(500, f"Erreur interne : {e}")
app/api/routes/data.py:41, 64                  JSONResponse({"error": str(e)}, status_code=500)
```

Le dépôt a un correctif explicite pour ce cas — `A-12`, `middleware.py:40` : *« retourne
un JSON 500 sans type interne »* — appliqué correctement ailleurs :

```python
raise HTTPException(500, f"Erreur interne ({err_id})")
# backtest.py:557, optimizer.py:244, derivatives.py:94 et 128, config_strategies.py:215
```

Les huit sites restants sont dans les deux modules qui manipulent le plus de chemins de
fichiers et de configuration : `/api/ml/*` (registre de modèles, entraînement) et
`/api/data/*` (Parquet, backfill). Un `FileNotFoundError` ou un `PermissionError` y
révèle l'arborescence du serveur ; une erreur SQLAlchemy y révèle des noms de tables.

L'endpoint est authentifié, donc l'exposition suppose un attaquant déjà porteur de la clé
API — d'où P2 et non P1. Reste que le correctif est déjà écrit dans le même dépôt et qu'il
suffit de l'appliquer.

**Effort** : 8 sites, ~20 minutes.

---

## SEC-02 — `ALLOW_WS_QUERY_KEY` autorise la clé API dans l'URL du WebSocket

**Sévérité P2 · PLAUSIBLE (lecture)**

`app/api/routes/ws.py:67` :

```python
return os.environ.get("ALLOW_WS_QUERY_KEY", "").strip().lower() in {...}
```

Le défaut est fermé (variable absente ⇒ refusé), ce qui est le bon choix. Mais lorsqu'elle
est activée, la clé API circule en **paramètre de requête**, c'est-à-dire à l'endroit où
un secret laisse le plus de traces : journaux d'accès nginx, historique de navigateur,
en-tête `Referer` vers un tiers, chaînes de proxy.

Le besoin est réel — l'API WebSocket du navigateur n'accepte pas d'en-tête personnalisé.
La réponse propre existe : un jeton éphémère à usage unique, obtenu par un `POST`
authentifié et valable quelques secondes, plutôt que la clé permanente.

**À arbitrer** : si l'option n'est activée par personne, la supprimer vaut mieux que la
documenter. Si elle l'est, le jeton éphémère est une demi-journée de travail.

---

## SEC-03 — `paper_mode` sans défaut sûr sur les vingt sites qui décident d'un ordre réel

**Sévérité P3 · CONFIRMÉ — aucun chemin exploitable aujourd'hui**

Détaillé en `07-LIVE-EXECUTION.md` (LIVE-04). Résumé : trois sites lisent
`.get("paper_mode", True)` (défaut sûr), une vingtaine lisent `.get("paper_mode")` — dont
**tous ceux qui décident d'envoyer un ordre**. Une clé absente y vaut `None`, donc
« mode réel ».

`config.DEFAULTS` garantit la clé après `load_config` (`config.py:71`, `:539-543`), donc
le risque est nul en l'état. Il est listé ici parce que le défaut le plus dangereux du
système repose sur une invariante posée ailleurs, non exprimée là où la décision se prend.

---

## Ce qui a été vérifié et tenu

Le détail, parce qu'un rapport de sécurité qui ne liste que les défauts donne une image
inversée de la réalité ici.

### Authentification

- **`Depends(verify_api_key)` sur la quasi-totalité des 99 endpoints**, y compris les
  41 mutants. Vérification exhaustive faite par recensement des décorateurs.
- **`hmac.compare_digest`** (`helpers.py:100`) — comparaison en temps constant, pas `==`.
- **Repli fermé** — sans clé configurée, l'accès est refusé hors `127.0.0.1` / `localhost`
  / `::1`, avec un message qui indique le correctif (`helpers.py:81-93`). Le défaut est
  restrictif, pas permissif.
- **Longueur du jeton bornée** à 256 caractères *avant* comparaison (`helpers.py:96-97`).
- **`X-Forwarded-For` derrière liste blanche** — l'en-tête n'est lu que si le pair
  immédiat est dans `_trusted_proxies()` (`helpers.py:74-80`). Sans ce garde, n'importe
  quel client se déclarerait `127.0.0.1` et contournerait le repli localhost.
- **`METRICS_TOKEN`** protège `/metrics` séparément (`main.py:140-155`), avec `Bearer` ou
  `X-API-Key`, et le point d'entrée reste public seulement en dev sans clé.

### Transport et navigateur

- **Cookie d'API `HttpOnly; SameSite=Lax; Secure`** posé côté serveur Next
  (`frontend/src/app/api/[...path]/route.ts:111`) : la clé n'est pas lisible par le
  JavaScript de la page, et `SameSite=Lax` bloque l'envoi sur une requête inter-site
  mutante — le CSRF est fermé par construction, sans jeton dédié.
- **CORS en liste blanche** — localhost en dev, `ALLOWED_ORIGINS` en production
  (`middleware.py:151-161`). Le commentaire `S6-09` documente pourquoi le port 3000 a dû
  être ajouté après la migration Next.js.
- **Redirection HTTPS** conditionnée à `FORCE_HTTPS=1` (`middleware.py:60-66`).
- **Rate limiting** SlowAPI avec handler dédié pour `RateLimitExceeded`.

### Journalisation

- **En-tête de corrélation assaini** — `X-Request-ID` filtré à `[A-Za-z0-9._-]` et borné à
  64 caractères, avec la raison écrite noir sur blanc (`middleware.py:75-86`) : *« la
  valeur est écrite telle quelle dans les logs, et un identifiant porteur de sauts de
  ligne permettrait d'y injecter des entrées entières »*. L'injection de log est une
  classe de faille que presque personne ne traite ; elle l'est ici, correctement.
- **Handler d'exceptions global** — journalise le type et le message côté serveur avec le
  `correlation_id`, ne renvoie au client qu'un message générique (`middleware.py:39-52`).
- **Caviardage** — commit `9381177` de la fenêtre auditée (`S-05 caviardage`).

### Chaîne d'approvisionnement et exécution de code

- **Aucun `pickle` ni `joblib`** dans le chargement des modèles — format LightGBM natif
  (`ml/backend/persistence.py:3,17`, `model_registry.py:51`,
  `ml_dynamic_threshold.py:951`). C'est la fermeture d'une classe entière de RCE : un
  fichier de modèle n'est plus du code exécutable déguisé.
- **Aucun `eval`, `exec`, `os.system` ni `shell=True`** dans `app/` — vérifié par
  recensement. Les deux `subprocess` (`backtest_history.py:39`, `model_registry.py:185`)
  passent une liste d'arguments, avec `timeout` et `stderr=DEVNULL`, pour lire un SHA git ;
  `model_registry.py:174` prévoit même `GIT_COMMIT` / `SOURCE_DATE_EPOCH` pour éviter le
  sous-processus.
- **`pip-audit` en CI, bloquant** (`.gitlab-ci.yml`, `allow_failure` absent donc `false`),
  sur `requirements.txt` **et** `requirements-dev.txt`.
- **Dépendances directes épinglées à la version exacte**, avec la date de verrouillage et
  la justification de l'absence de lockfile documentées en tête de `requirements.txt`.
  `starlette` est épinglée explicitement malgré la règle, avec l'explication du cas.

### Secrets

- **Aucune clé, aucun jeton en dur** — le recensement `os.getenv` / `os.environ` croisé
  avec `key|secret|token|passw` ne remonte que `METRICS_TOKEN` et `ALLOW_WS_QUERY_KEY`,
  deux noms de variables, aucune valeur.
- **`config.py:549-559`** impose `strict_env` dès que `paper_mode` est faux : sortir du
  paper exige que l'environnement soit complet, et le message dit comment y déroger
  explicitement.
- **`config.py:622-631`** refuse `paper_mode=false` sans canal de notification externe —
  un HALT sans alerte serait un incident silencieux. Garde-fou opérationnel bien vu.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| SEC-01 | P2 | CONFIRMÉ | 8 endpoints exposent l'exception interne | 20 min |
| SEC-02 | P2 | PLAUSIBLE | Clé API en paramètre d'URL si `ALLOW_WS_QUERY_KEY` | ½ j ou suppression |
| SEC-03 | P3 | CONFIRMÉ | `paper_mode` sans défaut sûr sur 20 sites | 30 min |

**Aucun P0 ni P1.** Sur les huit domaines audités, c'est le seul dans ce cas.
