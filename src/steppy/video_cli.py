"""``steppy video check`` — one answer instead of three mid-render failures.

Video export has more external moving parts than anything else in Steppy:
Chromium, ffmpeg, ffprobe, a TTS provider, an API key that may or may not have
survived ``${VAR}`` interpolation, and possibly a voice model on disk. Each of
them fails at a different point in a render that takes minutes per guide, and
each failure looks unrelated to the others. This command checks all of them in
about five seconds.

A leaf module in the ``register_flaky`` mould: it imports ``_cli_common`` plus
stdlib at module scope and nothing else, so ``import steppy.cli`` stays free of
the video and TTS stacks (guard:
``tests/test_no_video_import_on_default_path.py``).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import typer

from ._cli_common import _cli_errors, _error, console

video_app = typer.Typer(help="Narrated video export utilities.", no_args_is_help=True)


def register_video(app: typer.Typer) -> None:
    """Attach the ``video`` command group to the root Typer app."""
    app.add_typer(video_app, name="video")


@video_app.command("check")
def check(
    config_file: Path = typer.Option(
        Path("config.yaml"),
        "--config",
        "-c",
        help="Path to config.yaml",
    ),
) -> None:
    """Verify everything ``--video`` needs before spending minutes finding out."""
    from .config import load_config

    with _cli_errors():
        console.print("\n[bold]Video export readiness[/bold]\n")

        try:
            config = load_config(config_file)
            options = config.video
        except Exception as exc:
            _error(f"could not load {config_file}: {exc}")
            raise typer.Exit(1)

        problems: list[str] = []
        _report("config", str(config_file), ok=True)

        # ── Encoder ──────────────────────────────────────────────────────
        from .video import VideoRenderError, find_ffmpeg

        try:
            ffmpeg = find_ffmpeg(options)
            _report("ffmpeg", f"{ffmpeg}{_version_suffix(ffmpeg)}", ok=True)
        except VideoRenderError as exc:
            _report("ffmpeg", "not found", ok=False)
            problems.append(str(exc))

        probe = shutil.which("ffprobe")
        _report("ffprobe", probe or "not found (only needed for non-WAV narration)", ok=bool(probe))

        # ── Browser ──────────────────────────────────────────────────────
        ok, detail = _check_chromium()
        _report("chromium", detail, ok=ok)
        if not ok:
            problems.append("Chromium is unavailable: python -m playwright install chromium")

        # ── Narration ────────────────────────────────────────────────────
        from .tts import describe, readiness

        _report("tts provider", describe(options.tts), ok=True)
        trouble = readiness(options.tts)
        if trouble:
            _report("tts readiness", "not ready", ok=False)
            problems.append(trouble)
        else:
            voices = _voice_summary(options.tts, config.locales)
            _report("tts readiness", voices, ok=True)

        # ── Caches ───────────────────────────────────────────────────────
        from .captures import resolve_output_dir

        root = Path.cwd()
        video_cache = resolve_output_dir(root, options.cache_dir, ".steppy/video-cache")
        tts_cache = resolve_output_dir(root, options.tts.cache_dir, ".steppy/tts-cache")
        _report("video cache", _cache_detail(video_cache, options.cache, ".mp4"), ok=True)
        _report("tts cache", _cache_detail(tts_cache, options.tts.cache, ".wav"), ok=True)

        console.print()
        if problems:
            for problem in problems:
                _error(problem)
            raise typer.Exit(1)
        console.print("[green]OK[/green] ready to render — steppy build --video / steppy guide --video\n")


@video_app.command("cache")
def cache(
    config_file: Path = typer.Option(Path("config.yaml"), "--config", "-c", help="Path to config.yaml"),
    prune_older_than: Optional[float] = typer.Option(
        None,
        "--prune-older-than",
        metavar="DAYS",
        help="Drop entries not used in DAYS days.",
    ),
    max_size: Optional[float] = typer.Option(
        None,
        "--max-size",
        metavar="MB",
        help="Evict least-recently-used entries until each cache fits in MB.",
    ),
    clear_all: bool = typer.Option(False, "--clear", help="Remove every entry."),
    tts_only: bool = typer.Option(False, "--tts", help="Only the narration clip cache."),
    video_only: bool = typer.Option(False, "--video", help="Only the rendered video cache."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation for --clear."),
) -> None:
    """Inspect or reclaim the render caches.

    Both are content-addressed and therefore grow forever: every edited sentence
    leaves its old clip behind, and a Piper upgrade orphans all of them at once.
    With no flags this only reports.
    """
    from .captures import resolve_output_dir
    from .config import load_config
    from .video_cache import Reclaimed, clear, prune, stats, sweep_scratch

    with _cli_errors():
        if tts_only and video_only:
            _error("--tts and --video are mutually exclusive; omit both to act on each cache.")
            raise typer.Exit(2)

        try:
            options = load_config(config_file).video
        except Exception as exc:
            _error(f"could not load {config_file}: {exc}")
            raise typer.Exit(1)

        root = Path.cwd()
        targets = []
        if not tts_only:
            targets.append(("video", resolve_output_dir(root, options.cache_dir, ".steppy/video-cache"), ".mp4"))
        if not video_only:
            targets.append(("tts", resolve_output_dir(root, options.tts.cache_dir, ".steppy/tts-cache"), ".wav"))

        if clear_all and not yes:
            names = " and ".join(name for name, _, _ in targets)
            if not typer.confirm(f"Remove every entry from the {names} cache?"):
                console.print("[yellow]Aborted[/yellow] — nothing was removed.")
                raise typer.Exit(0)

        console.print("\n[bold]Render caches[/bold]\n")
        for name, path, suffix in targets:
            # Scratch is always swept: a staging file a day old belongs to no
            # live build, and leaving it costs disk while helping nobody.
            swept = sweep_scratch(path)
            if clear_all:
                freed = clear(path, suffix)
            elif prune_older_than is not None or max_size is not None:
                freed = prune(
                    path,
                    suffix,
                    older_than_days=prune_older_than,
                    max_bytes=int(max_size * 1024 * 1024) if max_size is not None else None,
                )
            else:
                freed = Reclaimed(0, 0)

            after = stats(path, suffix)
            if not after.exists:
                summary = "not created yet"
            else:
                summary = f"{_entries(after.entries)}, {_bytes(after.size)}"
                if after.newest and after.oldest:
                    summary += f" [dim](last used {_ago(after.newest)} ago, oldest {_ago(after.oldest)})[/dim]"
            _report(f"{name} cache", summary, ok=True)
            console.print(f"       [dim]{after.root}[/dim]")
            if freed.entries:
                console.print(f"       [green]reclaimed[/green] {_entries(freed.entries)}, {_bytes(freed.size)}")
            if swept.entries:
                console.print(f"       [dim]swept {swept.entries} scratch file(s), {_bytes(swept.size)}[/dim]")
            if after.scratch_files:
                console.print(
                    f"       [dim]{after.scratch_files} scratch file(s) held back "
                    f"({_bytes(after.scratch_size)}) — a build may still own them[/dim]"
                )
        console.print()


def _entries(count: int) -> str:
    return f"{count} entry" if count == 1 else f"{count} entries"


def _bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover - unreachable, the loop returns


def _ago(when: float) -> str:
    seconds = max(0.0, time.time() - when)
    for limit, div, unit in ((3600, 60, "min"), (86400, 3600, "h"), (float("inf"), 86400, "d")):
        if seconds < limit:
            return f"{seconds / div:.0f} {unit}"
    return "?"  # pragma: no cover - the last bucket is unbounded


def _report(label: str, detail: str, *, ok: bool) -> None:
    mark = "[green]OK[/green]  " if ok else "[red]FAIL[/red]"
    console.print(f"  {mark} [dim]{label:<14}[/dim] {detail}")


def _version_suffix(binary: str) -> str:
    try:
        proc = subprocess.run([binary, "-version"], capture_output=True, timeout=20)
        first = proc.stdout.decode("utf-8", "replace").splitlines()[0]
        return f"  [dim]({first.split(' Copyright')[0].strip()})[/dim]"
    except Exception:
        return ""


def _check_chromium() -> tuple[bool, str]:
    """Launch and close Chromium — the only check that actually proves it works."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return False, f"playwright not installed ({exc})"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            version = browser.version
            browser.close()
        return True, f"launches  [dim]({version})[/dim]"
    except Exception as exc:
        return False, f"cannot launch ({str(exc).splitlines()[0]})"


def _voice_summary(tts, locales: list[str]) -> str:
    if tts.provider == "none":
        return "captions only"
    missing = [loc for loc in locales if not tts.voice_for(loc)]
    if missing:
        # Not a failure: a provider may have a usable default voice. But a
        # multilingual project almost certainly meant to set one per locale.
        return f"voices set for {len(locales) - len(missing)}/{len(locales)} locales (missing: {', '.join(missing)})"
    return f"voices set for all {len(locales)} locale(s)"


def _cache_detail(path: Path, enabled: bool, suffix: str) -> str:
    if not enabled:
        return "disabled — every run re-renders (and re-bills a paid provider)"
    if not path.exists():
        return "empty  [dim](steppy video cache)[/dim]"
    from .video_cache import stats

    got = stats(path, suffix)
    return f"{_entries(got.entries)}, {_bytes(got.size)}  [dim](steppy video cache)[/dim]"
