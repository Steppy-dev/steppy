"""PDF export — headless-Chromium print-to-PDF over already-generated guide HTML.

Steppy never lays out a PDF itself. The guide templates already carry
``@media print`` rules (hide the animated cursor, keep a step on one page, cap
image height so a tall capture is scaled instead of clipped), so a PDF is just
"print the page we already render". That keeps one source of truth for the
layout and means a themed project's PDFs inherit its theme for free.

A **leaf module**: stdlib + ``models`` only, and ``playwright`` is imported
inside :func:`render_pdfs` so importing ``steppy.cli`` stays cheap
(guard: ``tests/test_no_playwright_import_on_default_path.py``).

Two invariants worth keeping if you touch this:

* **Pages are loaded over ``file://``.** Guide HTML references its screenshots
  with *relative* URLs; ``page.set_content`` would resolve them against
  ``about:blank`` and every image would be blank.
* **Lazy images are forced eager and awaited.** Both guide templates emit
  ``loading="lazy"``; images below the fold are the classic reason a
  print-to-PDF comes out with empty stages.
"""

from __future__ import annotations

import html as _html
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .models import PdfConfig


class PdfRenderError(Exception):
    """Fatal PDF-export failure — playwright missing, or Chromium unlaunchable.

    Per-page failures are *not* this: they land in a :class:`PdfResult` so one
    broken guide can't cost the other 149.
    """


@dataclass(frozen=True)
class PdfJob:
    """One page to print. ``title`` is only used for the footer."""

    html: Path
    pdf: Path
    title: str = ""


@dataclass(frozen=True)
class PdfResult:
    job: PdfJob
    ok: bool
    error: Optional[str] = None


# Chromium renders header/footer templates in their own document: external
# styles do not apply and an unset font-size renders at zero. Only the special
# classes (pageNumber / totalPages / title / date / url) are substituted.
_EMPTY_HEADER = "<span></span>"

_FOOTER_STYLE = (
    "width:100%;font-size:9px;color:#666;padding:0 12mm;"
    "font-family:-apple-system,'Segoe UI',Roboto,sans-serif;"
    "display:flex;justify-content:space-between;"
)

# Flip lazy images to eager so Chromium starts loading the ones below the fold.
_EAGER_IMAGES_JS = """() => {
  for (const img of document.images) {
    if (img.loading === 'lazy') { img.loading = 'eager'; }
  }
}"""

_IMAGES_COMPLETE_JS = "() => Array.from(document.images).every(img => img.complete)"

_BROWSER_HINT = "Chromium is required for PDF export. Install it with:\n  python -m playwright install chromium"


def _footer_template(title: str) -> str:
    """Footer with the guide title on the left and ``N / M`` on the right."""
    return (
        f'<div style="{_FOOTER_STYLE}">'
        f"<span>{_html.escape(title)}</span>"
        '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span>'
        "</div>"
    )


def pdf_options(options: PdfConfig, title: str = "") -> dict:
    """Map a :class:`~steppy.models.PdfConfig` onto ``page.pdf()`` kwargs.

    Split out from :func:`render_pdfs` so the mapping is testable without a
    browser. ``path`` is added by the caller.
    """
    kwargs: dict = {
        "format": options.format,
        "landscape": options.landscape,
        "print_background": options.print_background,
        "scale": options.scale,
        "margin": {
            "top": options.margin.top,
            "right": options.margin.right,
            "bottom": options.margin.bottom,
            "left": options.margin.left,
        },
    }
    if options.page_numbers:
        kwargs["display_header_footer"] = True
        # Without an explicit header template Chromium prints its own
        # (date + document title), which is not what "page numbers" asked for.
        kwargs["header_template"] = _EMPTY_HEADER
        kwargs["footer_template"] = _footer_template(title)
    return kwargs


def _default_warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def render_pdfs(
    jobs: Sequence[PdfJob],
    options: Optional[PdfConfig] = None,
    *,
    on_done: Optional[Callable[[PdfResult], None]] = None,
    on_warning: Optional[Callable[[str], None]] = None,
) -> list[PdfResult]:
    """Print every job to PDF, reusing one browser and one page for the batch.

    A guide project is a cartesian of scenarios × locales × layouts, so the
    batch is routinely in the hundreds — a browser launch per file would
    dominate the runtime.

    ``on_done`` is invoked after each job so callers can stream progress.
    Returns one :class:`PdfResult` per job, in order. Raises
    :class:`PdfRenderError` only when the browser itself can't start.
    """
    if not jobs:
        return []

    options = options or PdfConfig()
    warn = on_warning or _default_warn

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - playwright is a hard dep
        raise PdfRenderError(f"playwright is not installed: {exc}") from exc

    results: list[PdfResult] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                raise PdfRenderError(f"Could not launch Chromium: {exc}\n{_BROWSER_HINT}") from exc
            try:
                page = browser.new_page()
                for job in jobs:
                    result = _render_one(page, job, options, warn)
                    results.append(result)
                    if on_done:
                        on_done(result)
            finally:
                browser.close()
    except PdfRenderError:
        raise
    except Exception as exc:  # driver startup / teardown failures
        raise PdfRenderError(f"PDF export failed: {exc}\n{_BROWSER_HINT}") from exc

    return results


def _render_one(page, job: PdfJob, options: PdfConfig, warn: Callable[[str], None]) -> PdfResult:
    """Print a single job. Never raises — failures come back as a result."""
    try:
        if not job.html.is_file():
            return PdfResult(job=job, ok=False, error=f"source HTML not found: {job.html}")

        page.goto(job.html.resolve().as_uri(), wait_until="load", timeout=options.timeout)
        page.evaluate(_EAGER_IMAGES_JS)
        try:
            page.wait_for_function(_IMAGES_COMPLETE_JS, timeout=options.timeout)
        except Exception:
            # A PDF with one missing screenshot beats no PDF at all — say so
            # and print anyway.
            warn(f"{job.html.name}: some screenshots did not finish loading within {options.timeout}ms")

        job.pdf.parent.mkdir(parents=True, exist_ok=True)
        page.pdf(path=str(job.pdf), **pdf_options(options, job.title))
        return PdfResult(job=job, ok=True)
    except Exception as exc:
        return PdfResult(job=job, ok=False, error=str(exc))
