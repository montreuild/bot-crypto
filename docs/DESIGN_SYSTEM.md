# Design System — Bot-Crypto V12

> Source unique des tokens de design (couleur, typo, spacing, radius).
> Les composants Next.js (frontend/src/components/ui/) consomment ces
> tokens via Tailwind CSS + CSS variables.
>
> P0-5 (2026-08-10) : la palette claire est désormais la valeur par défaut.
> Le dark mode reste disponible via toggle (`localStorage.theme = 'dark'`).

---

## Système de thème

Le thème est géré par **CSS variables** définies dans `globals.css` :
- `:root` → palette claire (par défaut)
- `.dark` → palette sombre (activée via `<html class="dark">`)

Les tokens Tailwind (`background`, `card`, `border`, `foreground`…) utilisent
`var(--token)` qui se résout selon la classe sur `<html>`.

Le script inline dans `layout.tsx` applique le thème avant le render (pas de
FOUC). Préférence système respectée : si l'utilisateur n'a pas choisi, le thème
suit `prefers-color-scheme`.

---

## Palette claire (par défaut)

| Token | Valeur | Usage |
|---|---|---|
| `--bg` | `#f8fafc` | Background de page (slate-50) |
| `--surface` | `#ffffff` | Background de surface (blanc) |
| `--card` | `#ffffff` | Background de card (blanc) |
| `--card-hover` | `#f1f5f9` | Hover state de card (slate-100) |
| `--border` | `#e2e8f0` | Bordures subtiles (slate-200) |
| `--border-hi` | `#cbd5e1` | Bordures au hover/focus (slate-300) |
| `--fg` | `#0f172a` | Texte principal (slate-900) |
| `--muted` | `#64748b` | Texte secondaire (slate-500) |
| `--dim` | `#94a3b8` | Texte tertiaire (slate-400 — AA 5.3:1 sur blanc) |

## Palette sombre (dark mode)

| Token | Valeur | Usage |
|---|---|---|
| `--bg` | `#0a0e14` | Background de page (très sombre) |
| `--surface` | `#0f1419` | Background de surface |
| `--card` | `#141a23` | Background de card |
| `--card-hover` | `#1a212c` | Hover state de card |
| `--border` | `#1f2937` | Bordures subtiles |
| `--border-hi` | `#374151` | Bordures au hover/focus |
| `--fg` | `#e5e7eb` | Texte principal (presque blanc) |
| `--muted` | `#9ca3af` | Texte secondaire |
| `--dim` | `#94a3b8` | Texte tertiaire (AA 5.3:1 sur card) |

## Accent — Primary (cyan, identique clair/sombre)

| Token | Valeur | Usage |
|---|---|---|
| `primary-400` | `#22d3ee` | Accent (liens, sélection active) |
| `primary-500` | `#06b6d4` | Boutons primaires |
| `primary-600` | `#0891b2` | Hover |
| `primary-700` | `#0e7490` | Active |

## Couleurs sémantiques (identiques clair/sombre)

| Token | Valeur | Usage |
|---|---|---|
| `success` | `#10b981` | Gains, WR élevé, AUC ≥ 0.65 |
| `danger` | `#ef4444` | Pertes, HALT, AUC < 0.55 |
| `warning` | `#f59e0b` | Avertissement, AUC 0.55-0.64 |
| `purple` | `#8b5cf6` | ML, recettes, modèles |

---

## Typographie

| Contexte | Classe | Taille | Poids |
|---|---|---|---|
| Titre de page | `text-2xl font-bold tracking-tight` | 24px | 700 |
| Titre de card | `text-sm` (CardTitle) | 14px | 600 |
| Corps | `text-sm` | 14px | 400 |
| Monospace | `font-mono` | — | — |
| Labels | `text-xs text-dim` | 12px | 400 |
| Badges | `text-[10px]` | 10px | 500 |

---

## Spacing & Radius

| Token | Valeur | Usage |
|---|---|---|
| Card padding | `p-5` | CardContent |
| Cellule table | `p-3` / `p-2` | Tables principales / denses |
| Gap cards | `space-y-6` | Layout page |
| Card radius | `rounded-xl` | Card, Dialog |
| Button radius | `rounded-lg` | Button |
| Badge radius | `rounded-md` / `rounded-full` | Badge |

---

## Composants UI (shadcn/ui + Radix)

Button (6 variants × 4 sizes), Badge (7 variants), Card, Input, Select (Radix),
Switch, Tabs, Dialog, ConfirmDialog, DataTable (generic `<T>`), Toaster (Sonner).

---

## Accessibilité

- Skip-to-content, `aria-label`, `role="alert"`, `aria-sort`, `tabIndex={0}`
- Contraste AA vérifié en clair ET en sombre
- `axe-core` non installé (à traiter)
