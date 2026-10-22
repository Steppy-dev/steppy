export {
  GuideManifestSchema,
  SUPPORTED_MAJOR_VERSION,
  type Bbox,
  type Capture,
  type CaptureContent,
  type CaptureSize,
  type CursorBehavior,
  type CursorTimings,
  type Frame,
  type GuideManifest,
  type ManifestAction,
  type ManifestStep,
} from './schema.js';

export { captureLayoutKey, resolveStepContent } from './content.js';

export {
  parseManifest,
  type ParseError,
  type ParseErrorCode,
  type ParseResult,
} from './parse.js';
