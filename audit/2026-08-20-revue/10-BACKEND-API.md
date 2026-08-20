# 10 — Backend et API

Delta : `app/api/schemas.py` (+476/−3), `app/api/ws_tickets.py` (nouveau, 43),
`app/api/routes/ws.py` (+22/−42), plus des retouches sur 10 routes.

Le mouvement de fond est bon : les schémas Pydantic deviennent la source unique
du contrat, et `frontend/src/types/generated.ts` en est dérivé. Le garde-fou
qui protège cette dérivation est cependant partiel, et il casse l'image Docker
de test.

---

## API-01 — L'image Docker de test ne peut plus exécuter la suite (P2, CONFIRMÉ)

**Fichiers** : `Dockerfile:109-110`, `tests/test_openapi_contracts.py:43` et `:45`.
**Test ajouté par le delta** (+49 lignes).

### Le code

```python
# tests/test_openapi_contracts.py:43-45
from scripts.gen_frontend_types import _public_models
…
text = Path("frontend/src/types/generated.ts").read_text(encoding="utf-8")
```

Le test a donc deux dépendances hors de `app/` et `tests/`. L'étage `test` du
Dockerfile copie une liste **explicite et exhaustive** :

```dockerfile
COPY tests/ ./tests/                        # ligne 100
COPY scripts/audit_param_space.py ./scripts/   # ligne 109  ← un seul fichier
COPY frontend/next.config.mjs ./frontend/      # ligne 110  ← un seul fichier
```

Ni `scripts/gen_frontend_types.py`, ni `frontend/src/types/generated.ts` ne
sont dans l'image. L'étage `runtime` dont hérite `test` (lignes 54-72) ne copie
ni `scripts/` ni `frontend/`.

### Scénario d'échec

```
docker compose run tests
```

(`docker-compose.yml:109-114`, commande `python -m pytest -q -m "not slow"`).
Le test n'est pas marqué `slow` : il est donc collecté. Il échoue sur
`ModuleNotFoundError: No module named 'scripts.gen_frontend_types'`, ou sur
`FileNotFoundError` pour `generated.ts` si l'import passait.

L'étage `test` du Dockerfile part de `runtime` précisément pour tester ce qui
part en production. Ce chemin de vérification n'est plus vert.

**La CI GitHub n'est pas affectée** : elle lance `pytest` directement sur
l'exécuteur (`ci.yml:49`), où l'arborescence complète est présente. Le défaut
touche le chemin Docker, utilisé en local et en préproduction.

### Vérification

**CONFIRMÉ** par lecture de la liste exhaustive des `COPY` du Dockerfile,
croisée avec les deux dépendances du test. **L'image n'a pas été construite** —
la conclusion découle de la liste de copie, qui est explicite et sans
joker.

Périmètre vérifié : `grep -rn "from scripts\.\|import scripts" tests/` ne
renvoie que cette ligne, et `test_legacy_redirects.py` — l'autre test qui lit
`frontend/` — dépend de `next.config.mjs`, **qui est copié**. Le Dockerfile
copie donc bien, délibérément, les fichiers frontend nécessaires aux tests :
ceux du nouveau test ont simplement été oubliés.

### Correctif proposé

Deux lignes dans le Dockerfile :

```dockerfile
COPY scripts/gen_frontend_types.py ./scripts/
COPY frontend/src/types/generated.ts ./frontend/src/types/
```

**Effort** : 15 min, avec un `docker compose run tests` de vérification.

### Délégation IA

> `tests/test_openapi_contracts.py` importe `scripts.gen_frontend_types` et lit
> `frontend/src/types/generated.ts`. L'étage `test` du `Dockerfile` ne copie que
> `scripts/audit_param_space.py` et `frontend/next.config.mjs` : le test échoue
> dans l'image, alors qu'il passe en CI GitHub (qui lance pytest sur
> l'arborescence complète).
> Ajouter les deux `COPY` manquants à l'étage `test`, sur le modèle des
> lignes 109-110 existantes. Critère d'acceptation :
> `docker compose run --rm tests` doit passer, y compris
> `tests/test_openapi_contracts.py`.

---

## API-02 — Le garde-fou de dérive des contrats ne vérifie que les noms (P2, CONFIRMÉ)

**Fichier** : `tests/test_openapi_contracts.py:46-47`.

```python
for name in names:
    assert text.count(f"export interface {name} {{") == 1, name
```

Le test vérifie que chaque modèle Pydantic public a **une interface du même
nom** dans `generated.ts`. Il ne compare aucun champ.

### Scénario d'échec

Un champ est ajouté à `BacktestRunResponse` dans `app/api/schemas.py` sans que
`python scripts/gen_frontend_types.py` soit relancé. `generated.ts` conserve
l'ancienne interface : le nom est là, le test passe, `tsc --noEmit` passe (le
frontend n'utilise pas le champ). Le contrat a divergé silencieusement, et
c'est exactement le défaut que `generated.ts` était censé rendre impossible —
son en-tête le dit : « dérivé, pas recopié ».

Le risque est réel dans ce delta : `app/api/schemas.py` gagne **476 lignes**,
soit une réécriture large des contrats.

### Vérification

**CONFIRMÉ** par lecture : l'assertion est un `count` de déclaration
d'interface, sans lecture du corps.

### Correctif proposé

Comparer le fichier **entier** à la sortie du générateur :

```python
from scripts.gen_frontend_types import render
assert Path("frontend/src/types/generated.ts").read_text(encoding="utf-8") == render()
```

`render()` existe déjà (`scripts/gen_frontend_types.py:110`) et `main()` se
contente de l'écrire. Le test devient une vraie garde anti-dérive, et son
message d'échec indique quoi faire : relancer le générateur.

**Effort** : 30 min. À livrer avec API-01, qui touche le même test.

### Délégation IA

> Dans `tests/test_openapi_contracts.py`, remplacer l'assertion nom-par-nom par
> une comparaison du contenu complet de `frontend/src/types/generated.ts` avec
> `scripts.gen_frontend_types.render()`. Aujourd'hui le test ne vérifie que la
> présence des noms d'interfaces : un champ ajouté à un modèle Pydantic sans
> régénération passe inaperçu.
> Le message d'échec doit indiquer `python scripts/gen_frontend_types.py`.
> Vérifier d'abord que le fichier est actuellement en phase ; s'il ne l'est
> pas, régénérer dans le même commit.

---

## API-03 — Les bornes de validation ont été posées sur les enveloppes (CONFIRMÉ — amélioration)

**Fichier** : `app/api/schemas.py`, commit `f86d7a1`.

`Field(ge=…, le=…)` sur les enveloppes de risque et le drawdown global. Une
valeur hors bornes est désormais rejetée par Pydantic en 422 au lieu d'être
propagée jusqu'au moteur de risque. Contrôlé aux frontières, au bon endroit.

---

## API-04 — Les cinq routes chaudes déclarent un `response_model` (CONFIRMÉ — amélioration)

**Fichier** : `tests/test_openapi_contracts.py:13-30`.

`POST /api/backtest`, `GET /api/portfolio`, `GET /api/trades`, `GET /api/risk`,
`GET /api/optimize/results` déclarent leur modèle de réponse, et le test le
verrouille via `app.openapi()` — pas par lecture du source. C'est la bonne
méthode : il vérifie le schéma réellement servi.

---

## Ce qui a été vérifié sans rien trouver

- **Authentification** — `verify_api_key` (`app/api/helpers.py`) compte
  **115 arêtes entrantes** dans le graphe. Toutes les routes du delta ajoutant
  un endpoint (`POST /api/ws/ticket`, `ws_status`) portent bien
  `dependencies=[Depends(verify_api_key)]`.
- **`app/api/routes/ws.py`** — la suppression du repli `?api_key=` et son
  remplacement par un jeton éphémère est traitée dans `14-SECURITE.md`
  (SEC-01, SEC-02).
- **Parallélisme des routes** — `backtest.py:484` et `replay.py:128` bornent
  `max_workers` à `min(len(strats_to_run), 4)` : pas d'explosion de processus
  sur une requête à beaucoup de stratégies.
- **`app/api` hors périmètre mypy** — seul `ws_tickets.py` est type-vérifié en
  CI. Voir `TEST-02` dans `15-TESTS-CI.md`.
- **Divergence des deux pipelines** — `.gitlab-ci.yml` impose
  `--cov-fail-under=64` sur pytest, absent de `.github/workflows/ci.yml`. Les
  deux pipelines n'appliquent donc pas le même critère. Sans conséquence
  aujourd'hui (GitHub est la référence), mais c'est une divergence à connaître.
