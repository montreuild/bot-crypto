# 14 — Sécurité

Le delta livre un durcissement réel de l'authentification WebSocket
(`SEC-02` du backlog précédent) : le repli `?api_key=` disparaît au profit d'un
jeton éphémère à usage unique. Le mécanisme est correct. Les deux constats
ci-dessous portent sur ses limites, pas sur sa validité.

---

## 1. Ce que le delta corrige

**Fichiers** : `app/api/routes/ws.py:65-84`, `app/api/ws_tickets.py` (nouveau).

Avant, une clé API permanente pouvait transiter en query string
(`?api_key=xxx`), sous réserve d'activer `ALLOW_WS_QUERY_KEY=1`. Une clé en
query string finit dans les journaux d'accès nginx, dans l'historique du
navigateur et dans les en-têtes `Referer`. Le repli est supprimé.

Le remplacement :

- `POST /api/ws/ticket`, protégé par `Depends(verify_api_key)`, délivre un
  jeton `secrets.token_urlsafe(24)` — **192 bits d'entropie** ;
- durée de vie 30 s, **usage unique** (`_tickets.pop`) ;
- purge des jetons expirés à chaque émission et à chaque consommation ;
- le cookie HttpOnly reste le chemin nominal, vérifié en premier par
  `hmac.compare_digest` (`ws.py:80`).

C'est la bonne construction. Un jeton intercepté dans un journal d'accès est
inutilisable : il a expiré, ou il a déjà été consommé par le handshake légitime.

---

## SEC-01 — Le registre de jetons est local au processus (P2, CONFIRMÉ)

**Fichier** : `app/api/ws_tickets.py:18-19`.

```python
_lock = threading.Lock()
_tickets: Dict[str, float] = {}
```

Un dictionnaire en mémoire, propre au processus.

**Ce n'est pas un défaut aujourd'hui.** J'ai vérifié la configuration de
déploiement : l'API démarre par `python cli.py --paper`
(`Dockerfile:88`, `docker-compose.yml:52`), en **process unique** — aucun
`--workers`, aucun gunicorn, aucun appel à `uvicorn.run(workers=…)` dans
`app/` ni `cli.py`. Émission et consommation ont donc toujours lieu dans le
même processus.

**Scénario d'échec futur** — le jour où l'API passe derrière plusieurs
workers, ou est répliquée sur plusieurs conteneurs : un `POST /api/ws/ticket`
servi par le worker A crée le jeton dans la mémoire de A ; le handshake
`/ws?ticket=…` peut être routé vers le worker B, qui ne le connaît pas et
renvoie 4403. L'échec est **intermittent et proportionnel au nombre de
workers** — le pire profil à diagnostiquer.

Le frontend redemande un jeton à chaque tentative de reconnexion
(`ws-provider.tsx:93`), donc l'utilisateur verrait une connexion temps réel qui
échoue une fois sur deux sans message clair.

**Vérification** — **CONFIRMÉ** pour la nature process-locale (lecture du
module) et pour la configuration mono-processus actuelle (lecture du
Dockerfile, du `docker-compose.yml` et recherche exhaustive de `workers=` dans
`app/` et `cli.py`).

**Correctif proposé** — ne rien faire maintenant, mais **consigner la
contrainte** : ajouter au module une note explicite indiquant que le stockage
en mémoire impose un déploiement mono-processus, et prévoir Redis (ou la base
existante) comme support si la contrainte tombe.

**Effort** : 15 min pour la note ; 3 h pour un support partagé, le jour venu.

**Délégation IA** —
> Documenter dans `app/api/ws_tickets.py` que le registre `_tickets` est local
> au processus et que cela impose un déploiement de l'API en process unique
> (ce qui est le cas aujourd'hui : `cli.py --paper`, sans `--workers`).
> Ajouter un test qui échoue si `docker-compose.yml` ou le `Dockerfile`
> introduit un lancement multi-workers de l'API sans que le stockage des
> jetons ait été rendu partagé. Ne pas migrer vers Redis maintenant : la
> dépendance n'est pas justifiée par le déploiement actuel.

---

## SEC-02 — Aucune limite de débit sur l'émission de jetons (P3, CONFIRMÉ)

**Fichier** : `app/api/routes/ws.py:197-201`.

```python
@router.post("/api/ws/ticket", dependencies=[Depends(verify_api_key)])
def ws_ticket():
    token, ttl = issue_ticket()
    return {"ticket": token, "expires_in": ttl}
```

L'endpoint est authentifié — il faut déjà la clé API pour l'atteindre — mais
il n'est pas limité en débit.

**Scénario d'échec** — un client authentifié bogué, ou une boucle de
reconnexion emballée, appelle l'endpoint en rafale. Chaque appel insère une
entrée dans `_tickets`. La purge (`_purge_locked`) ne retire que les jetons
**expirés** : à N appels par seconde, le dictionnaire se stabilise autour de
`30 × N` entrées. À 10 000 appels/s, cela fait 300 000 entrées, et
`_purge_locked` — qui parcourt tout le dictionnaire à chaque appel, sous verrou
— devient quadratique en charge.

L'impact est borné par le fait qu'il faut être authentifié : ce n'est pas un
vecteur de déni de service anonyme, mais une fragilité en cas de client
défaillant.

**Vérification** — **CONFIRMÉ** par lecture : `_purge_locked`
(`ws_tickets.py:38-41`) construit la liste complète des clés mortes à chaque
appel, sous le verrou global. **Non reproduit sous charge** : je n'ai pas
mesuré le point de bascule.

**Correctif proposé** — borner la taille du registre (par exemple 10 000
entrées, en rejetant au-delà), et ne purger qu'au-delà d'un seuil plutôt qu'à
chaque appel.

**Effort** : 1 h.

---

## Ce qui a été vérifié sans rien trouver

- **Secrets en dur** — aucune affectation de `api_key`, `secret`, `password`
  ou `token` à une valeur littérale dans les lignes ajoutées par le delta
  (`app/`, `frontend/src/`, `cli.py`).
- **Comparaison de la clé API** — `hmac.compare_digest` est utilisé
  (`ws.py:80`), avec une borne de longueur à 256 caractères avant comparaison.
  Pas de comparaison naïve à temps variable.
- **Longueur des jetons** — `consume_ticket` rejette au-delà de 128 caractères
  (`ws_tickets.py:31`) avant toute recherche : pas de coût attaché à une
  entrée arbitrairement longue.
- **Application de l'authentification** — `verify_api_key` a **115 arêtes
  entrantes** dans le graphe. Les deux endpoints ajoutés par le delta
  (`POST /api/ws/ticket`, `GET /api/ws/status`) la déclarent bien.
- **Bornes de validation** — le delta étend `tests/test_sec_hardening.py`
  (+14) avec `test_venue_envelope_bounds` : capital, `symbol_risk_pct` et
  `venue_risk_pct` sont bornés côté Pydantic et le test vérifie le rejet.
  Le contrôle est aux frontières, ce qui est le bon endroit.
- **`pip-audit`** — câblé dans les deux pipelines (`ci.yml:69`,
  `.gitlab-ci.yml`) avec `allow_failure: false`. Je ne l'ai pas exécuté :
  cela demande un accès réseau à la base d'avis de sécurité.
- **Jeton en query string** — le jeton éphémère transite lui aussi par l'URL,
  donc par les journaux d'accès. C'est **assumé et acceptable** : à usage
  unique et 30 s de validité, une capture a posteriori est sans valeur. La
  critique qui valait pour `?api_key=` ne se transpose pas.
