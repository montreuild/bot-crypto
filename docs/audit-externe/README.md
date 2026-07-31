# Audit Technique Externe — Bot-Crypto V12

> Audit technique indépendant réalisé le 29 juillet 2026 par Z.ai
> (analyse multi-expert : ingénierie, crypto, marchés financiers, product).
>
> Ce dossier contient l'audit et le plan directeur qui en découle.

## Contenu

- `AUDIT_TECHNIQUE_BOT_CRYPTO_V12.md` — rapport source Markdown (~15 000 mots)
- `AUDIT_TECHNIQUE_BOT_CRYPTO_V12.pdf` — version PDF (54 pages, palette sobre)
- `diagrams/` — 3 diagrammes générés :
  - `architecture.png` — architecture en couches
  - `trading_flow.png` — flux de trading live en 12 étapes
  - `roadmap.png` — roadmap 8 sprints sur 16 semaines

## Méthode

Audit en deux passes :
1. **V1 « hors docs »** — analyse uniquement du code, configuration, tests, CI/CD.
   Reflète ce qu'un expert externe peut déduire sans documentation narrative.
2. **V2 « avec docs »** — re-analyse en intégrant toute la documentation (README,
   ARCHITECTURE.md, AUDIT.md, PRODUCTION_READINESS.md, 12 documents de `docs/`).
   Reflète la vision officielle.

Puis **autocritique comparative** V1 vs V2 avec tableau de synthèse, écarts
de notes, et recommandation finale.

## Constats clés

3 risques critiques convergents (V1 = V2) :
1. **🔴 Sur-risque sizing live** — `risk.compute_size` divise par ATR brut
   au lieu de la distance au stop. Risque réel 2,5× affiché.
2. **🔴 Bypass auth `X-Forwarded-For`** — spoofing trivial de l'IP cliente.
3. **🟠 Parité backtest↔live incomplète** — ne couvre que les formules
   monétaires (pas sizing ni timing).

## Notes synthétiques

| Dimension | V1 | V2 | Verdict |
|---|---|---|---|
| Architecture & Ingénierie | 3.5 | 4.0 | V2 plus fiable |
| Sécurité | 2.5 | 2.8 | V2 plus fiable |
| Financier & Risque | 3.0 | 3.3 | V2 plus fiable |
| Stratégie & Modèle | 3.0 | 3.3 | V2 plus fiable |
| UI/UX | 3.0 | 3.2 | V2 plus fiable |
| Product Management | 2.5 | 3.8 | V2 massivement plus fiable |
| **Moyenne** | **2.9** | **3.4** | V2 référence |

## Plan d'action

→ Voir `../PLAN_DIRECTEUR_AMELIORATIONS.md` pour le plan directeur
complet en 8 sprints × 2 semaines (173 story points).

## Source

Audit généré par Z.ai à partir du repo public
https://github.com/montreuild/bot-crypto — analyse statique + lecture de
510 fichiers + 12 documents de conception + 8 audits internes.
