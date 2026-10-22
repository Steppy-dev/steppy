# @steppy/react

A thin React wrapper around Steppy's framework-agnostic
[`<steppy-guide>`](../player/README.md) web component. Render an interactive,
multilingual product guide from a `guide.json` manifest produced by
`steppy build` (the `static` target — the default — emits one per interactive
guide).

> **Starter package.** This wrapper is intentionally minimal and will be
> extended (SSR guards, an inline-manifest prop, richer typing) as Steppy is
> implemented on real OSS React projects. Prefer the underlying web component
> directly if you need behaviour this wrapper doesn't yet expose.

## Install

```bash
npm install @steppy/react @steppy/player
```

`@steppy/react` depends on `@steppy/player` (which bundles the Lit element and
Lit itself); `react` is a peer dependency you already have.

## Usage

```tsx
import { SteppyGuide } from '@steppy/react';

export function Onboarding() {
  return (
    <SteppyGuide
      src="/assets/guides/onboarding/guide.json"
      locale="fr"
      variant="desktop"          // layout key
      template="overlay"         // 'overlay' | 'sidebar' | 'inline'
      onStep={(e) => console.log('step', e.index)}
      onComplete={() => console.log('done')}
    />
  );
}
```

### Props

| Prop | Type | Notes |
|---|---|---|
| `src` (required) | `string` | URL of the `guide.json` manifest (fetched). |
| `locale` | `string` | Active locale. |
| `variant` | `string` | Layout key (the element calls this `variant`). |
| `template` | `'overlay' \| 'sidebar' \| 'inline'` | Presentation. |
| `cursor` | `'arrow' \| 'hotspot' \| 'off'` | Cursor mode. |
| `autoplay` | `boolean` | Auto-advance. |
| `startIndex` | `number` | 1-based first step. |
| `hideLayoutSwitcher` | `boolean` | Suppress the built-in layout control. |
| `uiStrings` | `Record<string, Record<string, string>>` | Sparse per-locale UI overrides. |
| `className` / `style` | — | Standard styling passthrough. |

### Events → callbacks

`onReady`, `onStep`, `onHotspot`, `onLocaleChange`, `onVariantChange`,
`onComplete`, `onError` — each receives the corresponding `steppy:*`
CustomEvent's `detail`.

### Imperative navigation

```tsx
const ref = useRef<SteppyGuideHandle>(null);
// ref.current?.next() / .prev() / .restart() / .goTo(3)
<SteppyGuide ref={ref} src="…" />
```

## Develop

- `npm run build` — ESM bundle (`dist/index.js`) + types (`dist/index.d.ts`).
  Requires `@steppy/player` to be built first (`npm --prefix ../player run build`).
- `npm test` — Vitest smoke test under happy-dom.
