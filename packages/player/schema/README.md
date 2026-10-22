# Generated JSON Schema

`guide.schema.json` here is generated from the Zod schema at
[../src/manifest/schema.ts](../src/manifest/schema.ts).

Regenerate with:

```
npm run build:schema
```

The Zod schema is the source of truth. The JSON Schema is shipped for external
tooling (IDE completion, validation in downstream projects, docs). The runtime
validator in `parseManifest()` uses the Zod schema directly.

Cross-field rules (e.g. `defaultLocale` must appear in `locales`) live in a
`superRefine` block on the Zod schema and are **not** emitted into the JSON
Schema — JSON Schema lacks the expressiveness for them. Treat JSON Schema as
structural validation; `parseManifest()` is the authority.
