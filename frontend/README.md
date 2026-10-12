# DataOne Frontend

Next.js 16 + React 19 + Tailwind CSS 4. See `frontend/README.md` (this file)
for project basics and the **Theming** section below for the dark/light token
system.

## Theming

The frontend ships with a **dark + light theme** that is applied site-wide
without any new npm dependencies — it uses Tailwind v4's native
`@theme inline` directive plus a tiny client-side `ThemeProvider`.

### Default & persistence
- Default theme: **dark** (matches the original product look).
- Persistence: `localStorage` key `dp_theme` (values `"dark"` | `"light"`).
- A `<ThemeToggle />` button is mounted in:
  - the landing navbar (top-right),
  - the login form's top-right,
  - the dashboard header (next to the "5 DBs Connected" badge).

### How it works
1. `app/layout.tsx` ships an inline `<script>` in `<head>` that reads
   `localStorage.dp_theme` and applies the matching class to `<html>`
   **before React paints**. This avoids the flash of wrong theme.
2. `<ThemeProvider>` (in `src/lib/theme/ThemeProvider.tsx`) reads the
   already-applied class as its initial state, so the first React render
   matches the DOM — no hydration mismatch.
3. `<ThemeToggle>` flips the class, updates `localStorage`, and broadcasts
   across tabs via the `storage` event.

### Token map

All page components use semantic tokens (not raw `zinc-*` colors). Status
accents (`blue-*`, `emerald-*`, `red-*`, `amber-*`, `violet-*`) are kept as
raw Tailwind classes because they already carry semantic meaning.

| Token | Light | Dark | Use for |
|---|---|---|---|
| `bg-background` | `#ffffff` | `#09090b` | Page background |
| `bg-surface` | `#f9fafb` | `#18181b` | Card / panel background |
| `bg-surface-elevated` | `#ffffff` | `rgba(24,24,27,0.6)` | Card on tinted bg |
| `bg-surface-overlay` | `#f4f4f5` | `rgba(39,39,42,0.5)` | Row hover, chips |
| `text-fg` | `#18181b` | `#fafafa` | Primary text |
| `text-fg-muted` | `#3f3f46` | `#d4d4d8` | Secondary text |
| `text-fg-subtle` | `#71717a` | `#a1a1aa` | Tertiary text / placeholders |
| `border-border` | `#e4e4e7` | `#27272a` | Default border |
| `border-border-strong` | `#d4d4d8` | `#3f3f46` | Emphasised border |
| `bg-accent` / `text-accent` | `#4f46e5` | `#818cf8` | Primary accent |
| `bg-accent-soft` | `rgba(79,70,229,0.08)` | `rgba(129,140,248,0.12)` | Subtle accent wash |
| `bg-success` / `text-success` | `#059669` | `#10b981` | Success |
| `bg-warning` / `text-warning` | `#d97706` | `#f59e0b` | Warning |
| `bg-danger` / `text-danger` | `#dc2626` | `#ef4444` | Danger |

### Adding a new page

```tsx
// In your page component
<div className="bg-background text-fg">
  <div className="bg-surface-elevated border border-border rounded-xl p-4">
    <h2 className="text-fg">Title</h2>
    <p className="text-fg-muted">Subtitle</p>
  </div>
</div>
```

That's it — no theme switching required at the component level.

### Theme context API

```tsx
"use client";
import { useTheme } from "@/lib/theme";

function MyButton() {
  const { theme, setTheme, toggle } = useTheme();
  return <button onClick={toggle}>Current: {theme}</button>;
}
```

`useTheme()` throws if called outside `<ThemeProvider>` (the provider is
mounted once in `app/layout.tsx`, so this only fires on wiring mistakes).

### Tests

`src/lib/theme/__tests__/ThemeProvider.test.tsx` covers default theme,
localStorage persistence, toggle, class hygiene, and the
outside-provider error path.

---

## Project basics (unchanged)

This is a [Next.js](https://nextjs.org) project bootstrapped with
[`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

### Getting Started

```bash
npm run dev
```

Open [http://localhost:3011](http://localhost:3011) with your browser to see
the result.

### Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Start the Next.js dev server |
| `npm run build` | Production build |
| `npm run start` | Run the production build |
| `npm run lint` | ESLint (baseline: 7 errors / 11 warnings, all pre-existing) |
| `npm run test` | Vitest run |
