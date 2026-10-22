/**
 * Bridge test: Python emits a manifest, the TS validator approves it.
 *
 * Spawns Python in a subprocess to generate a manifest via
 * ``GuideAssembler.prepare_interactive_manifest`` (the build path), then runs
 * the result through ``parseManifest`` here. If both sides agree, the
 * contract between the build emitter and the player is locked.
 *
 * Skipped automatically if Python or the steppy package isn't importable —
 * keeps the JS-only Vitest run usable in environments without Python.
 */
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
import { describe, expect, it } from 'vitest';
import { parseManifest } from '../src/manifest/parse.js';

// __filename / __dirname aren't defined under "type": "module"; derive them.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
// packages/player/test/ → repo root (..,..,..)
const REPO_ROOT = resolve(__dirname, '..', '..', '..');

const PY_SCRIPT = String.raw`
import json
import struct
import sys
import tempfile
import zlib
from pathlib import Path
from textwrap import dedent

try:
    from steppy.guide import GuideAssembler
    from steppy.models import (
        AppAuthConfig, AppConfig, GlobalAuthConfig,
        LayoutConfig, ScreenshotConfig, SteppyConfig,
    )
    from steppy.parser import parse_scenario_file
except ImportError as exc:
    print("SKIP:" + str(exc), file=sys.stderr)
    sys.exit(2)


def _png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    return sig + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) \
         + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b"")


def _write_sidecar(folder, idx, bbox, selector, viewport=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"step-{idx:02d}.png").write_bytes(_png())
    payload = {
        "viewport": viewport or {"width": 1280, "height": 720},
        "target": {"kind": "single", "selector": selector, "bbox": bbox},
    }
    (folder / f"step-{idx:02d}.json").write_text(json.dumps(payload), encoding="utf-8")


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    scen = tmp / "s"
    scen.mkdir()
    (scen / "g.md").write_text(dedent("""\
        ---
        title: G
        tags: [user-guide-editor]
        guide:
          enabled: true
        ---

        ### 1. Click
        - role: editor
        - action: click
        - selector: '#a'
        - screenshot: editor
        - guide-text:
            fr:
              title: A
              text: A
            nl:
              title: B
              text: B

        ### 2. Desktop only
        - role: editor
        - action: click
        - selector: '#b'
        - screenshot: editor
        - layouts:
            - desktop
        - guide-text:
            fr:
              title: T
              text: Cliquez sur la ligne
            nl:
              title: T
              text: Klik op de rij

        ### 3. Mobile twin
        - role: editor
        - action: click
        - selector: '#c'
        - screenshot: editor
        - layouts:
            - mobile
        - guide-text:
            fr:
              title: T
              text: Touchez la carte
            nl:
              title: T
              text: Klik op de rij
        """), encoding="utf-8")
    sc = parse_scenario_file(scen / "g.md")

    shots = tmp / "shots"
    # Twin branch: desktop captures steps 1+2, mobile captures 1+3.
    for loc in ("fr", "nl"):
        for lay, indices in (("desktop", (1, 2)), ("mobile", (1, 3))):
            for idx in indices:
                _write_sidecar(
                    shots / sc.slug / loc / lay,
                    idx,
                    {"x": 10, "y": 20, "width": 50, "height": 30},
                    "#a",
                )

    cfg = SteppyConfig(
        apps={"editor": AppConfig(name="N", url="http://localhost", auth=AppAuthConfig(mode="auth0"))},
        auth=GlobalAuthConfig(),
        locales=["fr", "nl"],
        default_locale="fr",
        screenshot=ScreenshotConfig(layouts={
            "desktop": LayoutConfig(width=1440, height=900),
            "mobile": LayoutConfig(width=390, height=844),
        }),
    )
    a = GuideAssembler(cfg)

    def url(slug, idx, frame, locale, layout):
        stem = f"step-{idx:02d}" + (f"-{frame}" if frame else "")
        seg = f"{locale}/{layout}/" if layout else f"{locale}/"
        return f"./assets/{slug}/{seg}{stem}.png"

    m = a.prepare_interactive_manifest(
        scenario=sc, role="editor",
        screenshots_dir=shots,
        locales=["fr", "nl"], layouts=["desktop", "mobile"],
        default_locale="fr", default_layout="desktop",
        title={"fr": "G", "nl": "G"},
        cursor_timings={
            "pre_action_ms": 200, "action_ms": 300,
            "post_action_ms": 150, "travel_speed_px_per_ms": 1.5,
        },
        screenshot_url_fn=url,
    )
    print(json.dumps(m))
`;

function runPython(): { ok: true; json: string } | { ok: false; reason: string } {
  const cwd = process.env.STEPPY_REPO_ROOT ?? REPO_ROOT;
  let lastReason = 'no python executable found on PATH';
  for (const cmd of ['python', 'python3', 'py']) {
    const r = spawnSync(cmd, ['-c', PY_SCRIPT], { encoding: 'utf8', cwd });
    if (r.error && (r.error as NodeJS.ErrnoException).code === 'ENOENT') {
      lastReason = `${cmd} not found on PATH`;
      continue;
    }
    if (r.status === 2) {
      lastReason = `python skip signal: ${r.stderr?.trim() || 'unknown reason'}`;
      continue;
    }
    if (r.status !== 0) {
      return {
        ok: false,
        reason: `python exited ${r.status}: ${r.stderr || r.stdout || '(no output)'}`,
      };
    }
    return { ok: true, json: r.stdout };
  }
  return { ok: false, reason: lastReason };
}

describe('Python ↔ TS manifest bridge', () => {
  const pyResult = runPython();

  if (!pyResult.ok) {
    it.skip(`skipped: ${pyResult.reason.slice(0, 200)}`, () => {});
    return;
  }

  it('Python-emitted manifest passes the Zod validator', () => {
    const result = parseManifest(pyResult.json);
    if (!result.ok) {
      throw new Error(
        `parseManifest rejected the Python-emitted manifest:\n` +
          `code=${result.error.code}\n` +
          `message=${result.error.message}\n` +
          `issues=${JSON.stringify(result.error.issues, null, 2)}\n\n` +
          `manifest=${pyResult.json.slice(0, 800)}…`,
      );
    }
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.manifest.slug).toBeTypeOf('string');
      expect(result.manifest.steps).toHaveLength(2);
      expect(result.manifest.steps[0]!.interactive).toBe(true);
      // defaultTemplate defaults to 'overlay' (no default_template passed).
      expect(result.manifest.defaultTemplate).toBe('overlay');
      // Per-step title is emitted from guide-text (distinct from text).
      expect(result.manifest.steps[0]!.title).toBeDefined();
      expect(result.manifest.steps[0]!.title!.fr).toBe('A');
      // Captures must cover every (locale, layout)
      for (const step of result.manifest.steps) {
        for (const locale of result.manifest.locales) {
          for (const layout of result.manifest.layouts) {
            expect(step.captures[locale]?.[layout]).toBeDefined();
          }
        }
      }

      // The twin-branch step: each layout carries its own screenshot, and
      // mobile — whose French wording differs — carries its own content.
      const twin = result.manifest.steps[1]!;
      expect(twin.captures.fr!.desktop!.frames[0]!.screenshot).toContain('step-02.png');
      expect(twin.captures.fr!.mobile!.frames[0]!.screenshot).toContain('step-03.png');
      expect(twin.text.fr).toBe('Cliquez sur la ligne');
      expect(twin.captures.fr!.mobile!.content).toEqual({
        title: 'T',
        text: 'Touchez la carte',
        subtitle: null,
      });
      // Dutch reads identically in both layouts, so it inherits. Override
      // presence is per (locale, layout) cell, never uniform.
      expect(twin.captures.fr!.desktop!.content).toBeUndefined();
      expect(twin.captures.nl!.mobile!.content).toBeUndefined();
    }
  });
});
