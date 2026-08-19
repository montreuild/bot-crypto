# 09 — Backend API (FastAPI)

Périmètre : `app/api/*` (6 946 lignes) — 20 modules de routes, 99 endpoints dont 41
mutants, `main.py`, `middleware.py`, `helpers.py`, `state.py`, `schemas.py`,
`services/scanner_service.py`.

**Jugement d'ensemble.** L'authentification et les middlewares sont solides — mieux traités
que dans la plupart des projets de cette taille (détail en `14-SECURITE.md`). Le défaut
structurant est ailleurs : **l'API n'a aucun contrat de sortie**. 99 routes, zéro
`response_model`. Le contrat existe, mais il vit entièrement côté client, dans
`frontend/src/types/index.ts` (1 462 lignes écrites à la main) et
`frontend/src/lib/schemas.ts` (585 lignes de Zod). Le serveur peut changer la forme d'une
réponse sans qu'aucun test, aucun type, aucun outil ne s'en aperçoive avant que l'interface
ne casse à l'exécution.

---

## API-01 — Aucune route ne déclare de `response_model`

**Sévérité P1 · CONFIRMÉ (mesure)**

```
routes @router.{get,post,put,delete,patch} : 99
dont mutantes (post/put/delete)            : 41
occurrences de response_model              :  0
classes Pydantic dans schemas.py           : 12
```

Conséquences, dans l'ordre de gravité :

1. **Dérive de contrat silencieuse.** Renommer un champ dans `BacktestResult.to_dict()`
   (452 lignes, 45 clés) ne casse aucun test Python. La rupture apparaît à l'exécution
   dans le navigateur — au mieux via un `ZodError`, au pire par un `undefined` affiché
   comme `NaN` dans un tableau de PnL.
2. **L'OpenAPI ne documente rien.** `main.py:91` annonce la documentation interactive,
   mais sans `response_model` chaque schéma de réponse est vide. La page `/docs` liste des
   endpoints dont on ne peut pas savoir ce qu'ils renvoient.
3. **Aucun filtrage de sortie.** `response_model` sert aussi à *retirer* les champs non
   déclarés. Sans lui, tout ce que le dict porte part sur le réseau — y compris des
   structures internes comme `diagnostics`, `ml_info` ou `trades` complets
   (`backtest_result.py:441-449`).

Le dépôt a d'ailleurs déjà rencontré le problème : les correctifs `U-05` et `U-08` de la
fenêtre auditée (`9913bd2`, `601f43f`, `9df222f` — « *types FastAnalyse (kind, n)* »,
« *U-05 contrats restants* ») sont tous des réalignements manuels de types entre le
backend et le frontend. Ce travail se répétera à chaque évolution tant que le contrat ne
sera pas déclaré une seule fois, côté serveur.

**Correction, par étapes** :
1. Commencer par les 5 endpoints les plus consommés (`/api/backtest`, `/api/optimize/*`,
   `/api/portfolio`, `/api/trades`, `/api/risk`) — les modèles Pydantic dérivent
   directement des dicts existants.
2. Générer les types TypeScript depuis l'OpenAPI (`openapi-typescript`) au lieu de les
   écrire à la main : les 1 462 lignes de `types/index.ts` deviennent un artefact de build.
3. Un test de non-régression qui compare le schéma OpenAPI publié à un instantané.

**Effort** : 2 à 3 jours pour l'ensemble, ½ journée pour les 5 premiers endpoints —
lesquels couvrent l'essentiel du risque.

---

## API-02 — Six routes renvoient le message d'exception interne au client

**Sévérité P2 · CONFIRMÉ (recensement)**

Le dépôt a un correctif `A-12` explicite — `middleware.py:40` : *« retourne un JSON 500
sans type interne »* — et la plupart des routes suivent le bon motif :

```python
raise HTTPException(500, f"Erreur interne ({err_id})")     # backtest.py:557, optimizer.py:244,
                                                            # derivatives.py:94,128, config_strategies.py:215
```

Six autres exposent l'exception :

```
app/api/routes/ml.py:454, 503, 525, 569, 654      raise HTTPException(500, f"Erreur interne : {e}")
app/api/routes/backtest.py:79                     raise HTTPException(500, f"Erreur interne : {e}")
```

et deux renvoient `str(e)` dans le corps :

```
app/api/routes/data.py:41, 64                     JSONResponse({"error": str(e)}, status_code=500)
```

Ce que `{e}` peut contenir : chemins absolus du serveur, noms de tables, fragments de
requête SQL, extraits de configuration. Les routes concernées (`/api/ml/*`, `/api/data/*`)
manipulent des chemins de fichiers et le registre de modèles — c'est-à-dire exactement les
messages les plus bavards.

Le correctif est déjà écrit ailleurs dans le même dépôt : générer un `err_id`, journaliser
l'exception complète côté serveur, ne renvoyer que l'identifiant.

**Effort** : 8 sites, ~20 minutes.

---

## API-03 — 41 routes mutantes pour 12 modèles de validation d'entrée

**Sévérité P2 · partiellement corrigé** — les corps qui écrivent le risque
sont bornés : `VenueEnvelopeBody` (`capital > 0`, expositions (0, 1] /
(0, 0,10] / (0, 0,20]), `RiskConfigBody.min_slot_weight`,
`TradingParamsBody.max_drawdown_global`. Les actions sans corps
(`/bot/start`, `/optimize/cancel`) restent hors périmètre.

**Constat d'origine** :

`app/api/schemas.py` compte 228 lignes et 12 classes. Les 41 routes `POST`/`PUT`/`DELETE`
ne peuvent donc pas être toutes couvertes par un corps typé : le reste lit des paramètres
de requête ou des dicts bruts.

Ce n'est pas uniformément grave — beaucoup de ces routes sont des actions sans corps
(`/api/bot/start`, `/api/optimize/cancel`). Mais les routes qui **écrivent la
configuration** (`/api/config/trading`, `/api/config/risk`, `/api/config/strategy-params`,
`/api/settings/risk-preset`) modifient des paramètres qui pilotent le sizing et
l'exécution. Une valeur hors domaine y est plus coûteuse qu'un 422.

Le dépôt le sait : `tests/test_sec_hardening.py:186` associe explicitement
`(config_risk.update_risk_config, RiskConfigBody)` — la démarche est engagée, elle n'est
pas terminée.

**Correction** : compléter le typage des corps sur les routes de configuration en priorité,
avec des bornes (`Field(ge=…, le=…)`) sur les grandeurs de risque.

---

## API-04 — `scanner_service.py` : 778 lignes, 4 % de couverture

**Sévérité P2 · CONFIRMÉ (mesure)**

C'est le plus gros fichier de `app/api/` et le moins testé de tout le backend :

```
app/api/services/scanner_service.py    334 instructions    4 % couvertes
```

Il alimente `/api/scanner/*` — 10 endpoints, dont ceux qui produisent les signaux SMC
affichés dans l'interface. Les routes elles-mêmes sont à 20 %
(`app/api/routes/scanner.py`).

Le risque n'est pas théorique : un service de scan produit des **signaux de trading**. Une
régression y est invisible tant que personne ne compare les signaux affichés à ceux du
moteur.

---

## API-05 — Le module de routes le plus gros mélange registre, entraînement et jobs

**Sévérité P3 · Observation**

`app/api/routes/ml.py` : 654 lignes, 17 endpoints couvrant trois responsabilités distinctes
— registre de modèles (`/registry/*`, 8 endpoints), entraînement (`/train`, `/sweep`), et
gestion de jobs (`/jobs`). Même remarque pour `optimizer.py` (627 lignes, 11 endpoints).

Ce n'est pas un défaut de correction, c'est un coût de navigation : les cinq sites
d'API-02 sont tous dans `ml.py`, ce qui n'est pas un hasard — un fichier trop gros échappe
aux passes de nettoyage.

---

## Ce qui a été vérifié et tenu

- **Authentification systématique** — `Depends(verify_api_key)` est présent sur la quasi-
  totalité des 99 endpoints, y compris tous les mutants. Vérification exhaustive faite.
- **Comparaison en temps constant** — `hmac.compare_digest` (`helpers.py:100`), pas `==`.
- **Repli sûr sans clé configurée** — accès refusé hors `127.0.0.1` / `::1`, avec un
  message qui dit quoi faire (`helpers.py:81-93`). Le défaut est fermé, pas ouvert.
- **`X-Forwarded-For` derrière liste de proxys de confiance** (`helpers.py:74-80`) —
  l'en-tête n'est pas cru sur parole.
- **Longueur du jeton bornée** à 256 caractères avant comparaison (`helpers.py:96-97`).
- **Identifiant de corrélation assaini** — `[A-Za-z0-9._-]` uniquement et 64 caractères
  maximum, avec la justification exacte : la valeur est écrite dans les logs, un saut de
  ligne permettrait d'y injecter des entrées entières (`middleware.py:75-86`). C'est le
  genre de détail qu'on ne voit presque jamais traité.
- **Handler d'exceptions global** qui journalise avec le `correlation_id` et renvoie un 500
  propre sans type interne (`middleware.py:39-52`).
- **Rate limiting** SlowAPI, **GZip**, **CORS** en liste blanche (`ALLOWED_ORIGINS`),
  **redirection HTTPS** conditionnée à `FORCE_HTTPS`.
- **Ordre d'enregistrement des middlewares documenté** avec la raison (le dernier ajouté
  est le plus externe) — évite une régression subtile lors d'un futur ajout.

---

## Récapitulatif

| ID | Sévérité | Preuve | Constat | Effort |
|---|---|---|---|---|
| API-01 | **P1** | CONFIRMÉ | 0 `response_model` sur 99 routes | ½ j (top 5) → 3 j |
| API-02 | P2 | CONFIRMÉ | 8 sites renvoient l'exception interne | 20 min |
| API-03 | P2 | CONFIRMÉ | Enveloppes + DD global bornés (`VenueEnvelopeBody`) | fait |
| API-04 | P2 | CONFIRMÉ | `scanner_service.py` à 4 % de couverture | 2 j |
| API-05 | P3 | — | `ml.py` / `optimizer.py` trop gros | 1 j |
