# @steppy/vue

A thin Vue 3 wrapper around Steppy's framework-agnostic
[`<steppy-guide>`](../player/README.md) web component. Render an interactive,
multilingual product guide from a `guide.json` manifest produced by
`steppy build` (the `static` target — the default — emits one per interactive
guide).

> **Starter package.** This wrapper is intentionally minimal and will be
> extended (SSR guards, an inline-manifest prop, richer typing) as Steppy is
> implemented on real OSS Vue/PrimeVue projects. Prefer the underlying web
> component directly if you need behaviour this wrapper doesn't yet expose.

## Install

```bash
npm install @steppy/vue @steppy/player
```

`@steppy/vue` depends on `@steppy/player` (which bundles the Lit element and
Lit itself); `vue` is a peer dependency you already have.

## Usage

```vue
<script setup lang="ts">
import { SteppyGuide } from '@steppy/vue';
</script>

<template>
  <SteppyGuide
    src="/assets/guides/onboarding/guide.json"
    locale="fr"
    variant="desktop"        
    template="overlay"       
    @step="(d) => console.log('step', d.index)"
    @complete="() => console.log('done')"
  />
</template>
```

Because `SteppyGuide` is a render-function component (not a custom-element
passthrough), you do **not** need to configure
`app.config.compilerOptions.isCustomElement` for `<steppy-guide>`.

### Props

`src` (required), `locale`, `variant` (layout key), `template`
(`'overlay' | 'sidebar' | 'inline'`), `cursor` (`'arrow' | 'hotspot' | 'off'`),
`autoplay`, `startIndex`, `hideLayoutSwitcher`, `uiStrings`.

### Events

`@ready`, `@step`, `@hotspot`, `@locale-change`, `@variant-change`,
`@complete`, `@error` — each payload is the corresponding `steppy:*`
CustomEvent's `detail`.

### Imperative navigation

The component exposes `next()`, `prev()`, `restart()`, `goTo(index)`, and
`element` via a template ref.

## Develop

- `npm run build` — ESM bundle (`dist/index.js`) + types (`dist/index.d.ts`).
  Requires `@steppy/player` to be built first (`npm --prefix ../player run build`).
- `npm test` — Vitest smoke test under happy-dom.
