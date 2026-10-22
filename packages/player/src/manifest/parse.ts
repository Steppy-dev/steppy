import { ZodError } from 'zod';
import {
  GuideManifestSchema,
  SUPPORTED_MAJOR_VERSION,
  type GuideManifest,
} from './schema.js';

export type ParseErrorCode =
  | 'invalid_json'
  | 'version_missing'
  | 'version_unsupported'
  | 'schema_invalid'
  | 'fetch_failed'
  | 'screenshot_missing'
  | 'bad_attribute';

export interface ParseError {
  code: ParseErrorCode;
  message: string;
  issues?: Array<{ path: string; message: string }>;
}

export type ParseResult =
  | { ok: true; manifest: GuideManifest }
  | { ok: false; error: ParseError };

/**
 * Parse and validate a manifest from either a JSON string or a pre-parsed
 * value. Performs major-version compatibility check before structural
 * validation so version errors are reported with a clearer code.
 */
export function parseManifest(input: unknown): ParseResult {
  let value: unknown = input;
  if (typeof input === 'string') {
    try {
      value = JSON.parse(input);
    } catch (err) {
      return {
        ok: false,
        error: {
          code: 'invalid_json',
          message: err instanceof Error ? err.message : 'invalid JSON',
        },
      };
    }
  }

  const version = readVersion(value);
  if (version === null) {
    return {
      ok: false,
      error: {
        code: 'version_missing',
        message: 'manifest is missing a top-level "version" string',
      },
    };
  }
  const [majorStr] = version.split('.');
  const major = Number.parseInt(majorStr ?? '', 10);
  if (!Number.isFinite(major)) {
    return {
      ok: false,
      error: {
        code: 'version_missing',
        message: `manifest version "${version}" is not a valid <major>.<minor> string`,
      },
    };
  }
  if (major !== SUPPORTED_MAJOR_VERSION) {
    return {
      ok: false,
      error: {
        code: 'version_unsupported',
        message: `manifest version ${version} is not supported (player handles major ${SUPPORTED_MAJOR_VERSION}.x)`,
      },
    };
  }

  const result = GuideManifestSchema.safeParse(value);
  if (!result.success) {
    return {
      ok: false,
      error: schemaError(result.error),
    };
  }
  return { ok: true, manifest: result.data };
}

function readVersion(value: unknown): string | null {
  if (value === null || typeof value !== 'object') return null;
  const v = (value as { version?: unknown }).version;
  return typeof v === 'string' ? v : null;
}

function schemaError(err: ZodError): ParseError {
  return {
    code: 'schema_invalid',
    message: 'manifest failed schema validation',
    issues: err.issues.map((issue) => ({
      path: issue.path.map(String).join('.'),
      message: issue.message,
    })),
  };
}
