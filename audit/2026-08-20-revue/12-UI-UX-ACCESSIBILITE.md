# 12 — UI/UX et accessibilité

Le dépôt dispose d'un filet d'accessibilité automatisé sérieux : un job CI
dédié (`.github/workflows/ci.yml:154-186`) lance **axe sur 20 pages** via
`frontend/e2e/tests/a11y.spec.ts`, et il est vert.

Les constats ci-dessous portent donc sur ce qu'axe **ne peut pas** détecter :
l'état d'un contrôle personnalisé, et l'information portée par la seule
couleur.

---

## UX-01 — Le sélecteur de stratégies ne communique pas son état aux technologies d'assistance (P2, CONFIRMÉ)

**Fichier** : `frontend/src/components/ui/strategy-picker.tsx:72-92`.
**Composant ajouté par le delta** (113 lignes), et déployé partout dans le
Laboratoire (`d84bdf5 fix(lab): StrategyPicker identique partout`).

### Le code

```tsx
<button
  key={s}
  type="button"
  onClick={() => toggle(s)}
  className={cn(
    'px-3 py-1.5 rounded-lg text-xs font-mono border …',
    active
      ? 'bg-primary-500/15 text-primary-400 border-primary-500/40'
      : 'bg-card-hover text-muted border-border hover:border-border-hi',
  )}
>
```

C'est une case à cocher multiple déguisée en groupe de boutons. Il manque :

| Manque | Conséquence |
|---|---|
| `aria-pressed={active}` | L'état sélectionné n'est **pas annoncé** |
| `role="group"` + `aria-label` sur le conteneur | Les boutons sont annoncés isolément, sans dire de quoi ils font partie |
| Une marque non colorée de l'état | L'état repose entièrement sur `text-primary-400` contre `text-muted` |

### Scénario d'échec

Un utilisateur de lecteur d'écran ouvre le Laboratoire et parcourt la liste des
stratégies. Chaque élément est annoncé « bouton, opus_omnibus_v11 » —
**identique pour une stratégie sélectionnée et une non sélectionnée**. Il ne
peut ni savoir ce qui est actif, ni vérifier ce qu'il vient d'activer. Le
compteur « Stratégies (3/41) » est le seul retour, et il n'indique pas
*lesquelles*.

Même problème sans lecteur d'écran, pour un utilisateur daltonien : la
distinction actif/inactif est une nuance de couleur sur un fond translucide,
sans changement de forme, de graisse ni d'icône. C'est un manquement au
critère WCAG 1.4.1 (« Utilisation de la couleur »).

### Pourquoi la CI ne le voit pas

Axe ne signale pas l'absence d'`aria-pressed` sur un `<button>` : rien dans le
balisage ne lui dit qu'il s'agit d'une bascule. Et le critère 1.4.1 n'est pas
automatisable — axe vérifie le **contraste**, pas le fait qu'une information
soit portée uniquement par la couleur.

Le job `a11y` est donc légitimement vert. Ce constat n'est pas une défaillance
du filet, mais sa limite connue.

### Vérification

**CONFIRMÉ** par lecture complète du composant (113 lignes) :
`grep -n "role=\|aria-\|onKeyDown\|tabIndex" strategy-picker.tsx` ne renvoie
aucun attribut ARIA.

Comparaison utile : `symbol-search.tsx`, ajouté dans le **même** delta, est
exemplaire — `role="combobox"`, `aria-expanded`, `aria-controls`,
`aria-autocomplete`, `role="listbox"`, `role="option"`, `aria-selected`, et une
gestion de `onKeyDown`. L'écart entre les deux composants est un oubli, pas un
choix.

### Correctif proposé

```tsx
<div role="group" aria-label="Stratégies à tester">
  …
  <button
    type="button"
    aria-pressed={active}
    onClick={() => toggle(s)}
    …
  >
    {active && <Check className="w-3 h-3" aria-hidden="true" />}
    {s}
```

L'icône rend l'état perceptible sans recours à la couleur.

**Effort** : 1 h, plus un test.

### Délégation IA

> `frontend/src/components/ui/strategy-picker.tsx` est un sélecteur multiple
> construit avec des `<button>` sans aucun attribut ARIA : l'état sélectionné
> n'est annoncé à aucune technologie d'assistance, et il n'est distingué que
> par la couleur (WCAG 1.4.1).
> 1. Ajouter `aria-pressed={active}` sur chaque bouton de stratégie.
> 2. Envelopper la liste dans `role="group"` avec un `aria-label`.
> 3. Ajouter une marque visuelle non colorée de l'état sélectionné (icône
>    `Check` de `lucide-react`, `aria-hidden="true"`).
> Prendre `frontend/src/components/ui/symbol-search.tsx` comme modèle : il est
> correctement instrumenté.
> Étendre `components/ui/__tests__/strategy-picker.test.tsx` pour vérifier que
> `aria-pressed` reflète la sélection. Le job CI `a11y` doit rester vert.

---

## UX-02 — Des libellés sont rendus à 8,8 px (P2, CONFIRMÉ)

**Fichier** : `frontend/src/components/ui/strategy-picker.tsx:87` et `:98`.

```tsx
className="px-1 rounded text-[0.55rem] bg-cyan-500/15 text-cyan-300 …"
```

`0.55rem` vaut **8,8 px** à la taille de police racine par défaut. Ces badges
portent les timeframes recommandés — une information utile pour choisir un
couple stratégie/TF.

Les boutons « Toutes » et « Aucune » sont à `text-[10px]` (`:47`, `:56`).

### Scénario d'échec

Un utilisateur presbyte, ou sur un écran haute densité mal calibré, ne
distingue pas `15m` de `1h` sur les badges. Il sélectionne un timeframe non
recommandé sans voir l'avertissement `⚠` — lui-même rendu à 8,8 px.

### Vérification

**CONFIRMÉ** par lecture du code. **Non mesuré au rendu** : je n'ai pas
exécuté le frontend pour capturer la taille effective en pixels, qui dépend de
la taille de police racine de l'application.

### Correctif proposé

Plancher à `text-[0.6875rem]` (11 px) pour les badges, `text-xs` (12 px) pour
les boutons d'action. Et surtout, ne plus faire porter l'avertissement `⚠` par
un badge minuscule.

**Effort** : 30 min.

---

## UX-03 — L'avertissement de timeframe non recommandé n'est accessible que par survol (P2, CONFIRMÉ)

**Fichier** : `frontend/src/components/ui/strategy-picker.tsx:95-102`.

```tsx
<span
  className="px-1 rounded text-[0.55rem] …"
  title="Au moins un TF sélectionné n'est pas recommandé"
>
  ⚠
</span>
```

L'information complète vit dans un attribut `title` sur un `<span>`.

Un `title` n'est atteignable ni au clavier (le `<span>` n'est pas focusable),
ni de façon fiable par un lecteur d'écran, ni sur écran tactile — où le survol
n'existe pas. Le caractère `⚠` seul, sans texte alternatif, est annoncé de
façon variable selon les moteurs.

### Scénario d'échec

Sur tablette, un utilisateur sélectionne `opus_omnibus_v11` en 15 m alors que
la stratégie recommande 1 h et 4 h. Le `⚠` apparaît ; aucun appui ne révèle son
sens. L'optimisation part sur un couple non recommandé sans avertissement
compris.

### Vérification

**CONFIRMÉ** par lecture. Le dépôt dispose pourtant d'un composant
`Tooltip` accessible, **ajouté dans ce même delta**
(`frontend/src/components/ui/tooltip.tsx`, 50 lignes, exporté depuis
`components/ui/index.ts`). Il n'est simplement pas utilisé ici.

### Correctif proposé

Remplacer le `title` par le composant `Tooltip` du dépôt, et donner au
`⚠` un équivalent textuel :

```tsx
<Tooltip content="Au moins un TF sélectionné n'est pas recommandé">
  <span className="…" role="img" aria-label="Avertissement : timeframe non recommandé">⚠</span>
</Tooltip>
```

**Effort** : 45 min. À livrer avec UX-01 et UX-02, même fichier.

### Délégation IA

> Dans `frontend/src/components/ui/strategy-picker.tsx`, l'avertissement de
> timeframe non recommandé et les badges de TF ne portent leur sens que dans un
> attribut `title` sur un `<span>` — inatteignable au clavier et sur tactile.
> Remplacer par le composant `Tooltip` déjà présent dans
> `frontend/src/components/ui/tooltip.tsx` (exporté par
> `components/ui/index.ts`), et donner au caractère `⚠` un `aria-label`
> explicite. Remonter au passage les tailles `text-[0.55rem]` à au moins
> `text-[0.6875rem]`.
> Le job CI `a11y` doit rester vert et `vitest run` doit rester à 190 passés.

---

## Ce qui a été vérifié sans rien trouver

- **`symbol-search.tsx`** (273 lignes, nouveau) — accessibilité correcte :
  `role="combobox"`, `aria-expanded`, `aria-controls`, `aria-autocomplete`,
  `role="listbox"`, `role="option"`, `aria-selected`, navigation clavier via
  `onKeyDown`. C'est le modèle interne à suivre.
- **`tooltip.tsx`** (50 lignes, nouveau) — bâti sur
  `@radix-ui/react-tooltip`, qui gère focus, échappement et annonce. Le
  composant est bon ; il est seulement sous-utilisé.
- **Job CI `a11y`** — `npx playwright test a11y.spec.ts --project=chromium`
  sur 20 pages (`/trades`, `/audit`, `/data`, `/models`, `/portfolio`,
  `/bots`, les 4 onglets `/market`, les 5 onglets `/lab`, les 5 onglets
  `/settings`). Un rapport est archivé en artefact. Le filet est réel.
- **Job CI `visual`** — `visual.spec.ts` retouché dans le delta (+3/−1),
  toujours câblé.
- **Formatage des nombres** — le passage au format fr-FR
  (`1897580 fix(ui): UX-02`) est centralisé dans `lib/utils.ts` et testé
  (`utils.test.ts`, 125 lignes). Les séparateurs de milliers et les devises
  sont désormais cohérents entre les vues.
