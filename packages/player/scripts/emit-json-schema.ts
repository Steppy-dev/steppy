#!/usr/bin/env tsx
import { writeFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { zodToJsonSchema } from 'zod-to-json-schema';
import { GuideManifestSchema } from '../src/manifest/schema.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const outPath = resolve(__dirname, '../schema/guide.schema.json');

const jsonSchema = zodToJsonSchema(GuideManifestSchema, {
  name: 'GuideManifest',
  target: 'jsonSchema7',
});

mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, JSON.stringify(jsonSchema, null, 2) + '\n', 'utf8');

console.log(`Wrote ${outPath}`);
