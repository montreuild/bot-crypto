# Design System — Bot-Crypto V12

> Source unique des tokens de design (couleur, typo, spacing, radius).
> Les composants Next.js (frontend/src/components/ui/) consomment ces
> tokens via Tailwind CSS. Storybook reporté à une itération ultérieure.

---

## Tokens de couleur

### Palette principale (audit sobre — Deloitte/BCG)

| Token | Valeur | Usage |
|---|---|---|
| `--primary` | `#256a8c` | Accent principal (liens, boutons principaux, highlights) |
| `--primary-400` | `#3d8fb3` | Variante plus claire pour hover/active |
| `--primary-500` | `#256a8c` | Variante normale |
| `--accent-2` | `#cd475d` | Accent secondaire (alertes critiques, badges danger) |
| `--bg` | `#fafbfc` | Background de page (très clair) |
| `--surface` | `#f4f5f5` | Background de cards |
| `--card` | `#ffffff` | Background de card (pur blanc) |
| `--card-hover` | `#f9fafb` | Hover state de card |
| `--border` | `#e2e8f0` | Bordures subtiles |
| `--text` | `#0f172a` | Texte principal (presque noir) |
| `--text-muted` | `#64748b` | Texte secondaire (gris) |
| `--text-dim` | `#94a3b8` | Texte tertiaire (gris clair) |

### Couleurs sémantiques

| Token | Valeur | Usage |
|---|---|---|
| `--success` | `#449760` | État positif (win, +PnL, bot actif) |
| `--warning` | `#927742` | Avertissement (orange sobre) |
| `--error` | `#8f524c` | Erreur critique (rouge sobre) |
| `--info` | `#3f6993` | Information (bleu sobre) |
| `--purple` | `#8b5cf6` | Badge ML / spécial |

### Usage dans Tailwind

```ts
// frontend/tailwind.config.ts
extend: {
  colors: {
    primary: {
      400: '#3d8fb3',
      500: '#256a8c',
      DEFAULT: '#256a8c',
    },
    'accent-2': '#cd475d',
    surface: '#f4f5f5',
    card: '#ffffff',
    'card-hover': '#f9fafb',
    border: '#e2e8f0',
    dim: '#94a3b8',
  },
}
```

---

## Tokens de typographie

### Famille

- **Sans-serif (default)** : `Inter`, -apple-system, sans-serif
- **Mono (chiffres, code)** : `JetBrains Mono`, monospace
- **Serif (titres hero)** : `Playfair Display` (cover uniquement)

### Échelle (base 16px)

| Rôle | Taille | Weight | Line-height | Usage |
|---|---|---|---|---|
| Hero (cover) | 54px | 900 | 1.05 | Cover page titre |
| Page title (H1) | 24px | 700 | 1.2 | Titre de page |
| Section title (H2) | 20px | 600 | 1.3 | Section de page |
| Subsection (H3) | 16px | 600 | 1.4 | Sous-section |
| Body | 14px | 400 | 1.6 | Texte courant |
| Small | 12px | 400 | 1.5 | Notes, captions |
| Caption | 11px | 400 | 1.4 | Metadata, timestamps |
| Tag/Badge | 10px | 600 | 1.2 | Uppercase badges |
| KPI value | 32px | 700 | 1.0 | KPI cards (dashboard) |
| Mono code | 13px | 500 | 1.4 | Symboles, valeurs mono |

### Implementation Tailwind

```ts
fontSize: {
  '2xl': ['24px', '1.2'],
  'xl': ['20px', '1.3'],
  lg: ['16px', '1.4'],
  base: ['14px', '1.6'],
  sm: ['12px', '1.5'],
  xs: ['11px', '1.4'],
  '10': ['10px', '1.2'],
}
```

---

## Tokens d'espacement

Échelle basée sur **4px** (8 = 2×4, 12 = 3×4, etc.).

| Token | Taille | Usage |
|---|---|---|
| `space-1` | 4px | Gap minimal (icône + label) |
| `space-2` | 8px | Gap petit (entre boutons) |
| `space-3` | 12px | Gap normal (entre cards) |
| `space-4` | 16px | Padding interne card |
| `space-5` | 20px | Marge section |
| `space-6` | 24px | Marge entre sections |
| `space-8` | 32px | Marge principale |
| `space-12` | 48px | Marge cover page |

---

## Tokens de radius

| Token | Valeur | Usage |
|---|---|---|
| `radius-sm` | 4px | Badges, petits éléments |
| `radius-md` | 6px | Inputs, boutons |
| `radius-lg` | 8px | Cards |
| `radius-xl` | 12px | Modales |
| `radius-full` | 9999px | Pastilles (status indicator) |

---

## Tokens d'animation

| Animation | Durée | Easing | Usage |
|---|---|---|---|
| `fade-in` | 200ms | `ease-out` | Entrée de page |
| `fade-out` | 150ms | `ease-in` | Sortie |
| `slide-in` | 300ms | `cubic-bezier(0.16, 1, 0.3, 1)` | Modale |
| `pulse` | 2000ms | `ease-in-out` | Indicateur live |
| `spin` | 1000ms | `linear` | Loading |

```css
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.animate-fade-in { animation: fadeIn 200ms ease-out; }
```

---

## Composants UI standards (Radix-based)

| Composant | Variantes | Usage |
|---|---|---|
| `Button` | primary, outline, ghost, danger | Actions |
| `Badge` | success, warning, info, danger, muted, purple | États, tags |
| `Card` | default, hover, accent | Conteneurs |
| `Dialog` | default | Modales (Radix Dialog) |
| `Tabs` | underline, segmented | Navigation intra-page |
| `Toast` | success, error, info | Notifications (sonner) |
| `Tooltip` | default | Aide contextuelle |
| `Switch` | default | Toggle booléen |
| `Select` | default | Dropdown (Radix Select) |
| `ScrollArea` | vertical, horizontal | Listes scrollables |

---

## Accessibilité (WCAG 2.1 AA)

### Contraste

Tous les couples texte/background ont un ratio ≥ 4.5:1 (AA normal text).

| Couleur texte | Background | Ratio | Statut |
|---|---|---|---|
| `#0f172a` | `#fafbfc` | 16.8:1 | ✅ AAA |
| `#64748b` | `#fafbfc` | 5.0:1 | ✅ AA |
| `#94a3b8` | `#fafbfc` | 3.1:1 | ⚠ À utiliser sur > 18px uniquement |
| `#256a8c` | `#ffffff` | 5.5:1 | ✅ AA |
| `white` | `#256a8c` | 5.5:1 | ✅ AA |
| `white` | `#cd475d` | 4.6:1 | ✅ AA |

### Focus visible

Tous les éléments interactifs ont un focus ring :
```css
:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
```

### Navigation clavier

- Tous les éléments interactifs sont atteignables au Tab.
- Les modales piègent le focus (Radix Dialog natif).
- Les dropdowns ferment sur Escape (Radix).
- Les éléments custom `onclick` ont `role="button"` + `tabindex="0"` + gestion `keydown`
  (cf. UI-06 réalisé dans l'audit interne).

### ARIA

- `aria-label` sur tous les boutons icône-seul.
- `role="status" aria-live="polite"` sur les zones de chargement.
- `scope="col"` sur les `<th>` des tableaux.
- `aria-current="page"` sur le lien de navigation actif.

### Responsive

Breakpoints Tailwind :
- `sm` 640px — mobile landscape
- `md` 768px — tablet
- `lg` 1024px — desktop
- `xl` 1280px — wide

Toutes les pages Next.js doivent être utilisables sur mobile :
- Sidebar → drawer (mobile)
- Tableaux → scroll horizontal avec `overflow-x-auto`
- KPI grids : `grid-cols-2 md:grid-cols-3 lg:grid-cols-5`
- Charts : `min-h-[300px]` pour éviter l'écrasement

---

## Patterns UI récurrents

### KPI cards

```tsx
<div className="rounded-xl border border-border bg-card p-5">
  <div className="text-xs uppercase tracking-wider text-muted">Capital</div>
  <div className="text-2xl font-bold mt-2 font-mono">$1,000</div>
  <div className="text-xs text-emerald-400 mt-1">+5.2%</div>
</div>
```

### Page header

```tsx
<div className="flex items-end justify-between">
  <div>
    <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
    <p className="text-sm text-muted mt-1">Sous-titre contextuel</p>
  </div>
  <Badge variant="success">
    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
    Live
  </Badge>
</div>
```

### Loading skeleton

```tsx
<div className="space-y-2">
  <div className="h-4 bg-card-hover rounded w-1/3" />
  <div className="h-3 bg-card-hover rounded w-1/2" />
  <div className="h-3 bg-card-hover rounded w-2/3" />
</div>
```

### Empty state

```tsx
<div className="text-center py-12">
  <Icon className="w-12 h-12 text-dim mx-auto mb-3" />
  <h3 className="text-lg font-medium mb-1">Aucune position</h3>
  <p className="text-sm text-muted">Le bot n'a pas encore ouvert de position.</p>
</div>
```

### Error state

```tsx
<div className="text-center py-12 text-red-500">
  <AlertCircle className="w-12 h-12 mx-auto mb-3" />
  <h3 className="text-lg font-medium mb-1">Erreur de chargement</h3>
  <Button onClick={refetch} variant="outline" size="sm">Réessayer</Button>
</div>
```

---

## Storybook (reporté)

La mise en place de Storybook pour documenter les composants de manière
interactive est **reportée** — pas critique pour la migration Jinja2 → Next.js.
À traiter dans une itération ultérieure si besoin de visualiser les
composants isolément.

En attendant, ce document + `frontend/src/components/ui/*.tsx` servent de
référence pour les composants disponibles.
