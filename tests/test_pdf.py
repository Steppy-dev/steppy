"""Tests for `steppy.pdf` — the headless-Chromium print-to-PDF renderer.

Playwright is faked so the batching contract, the option mapping and the
error isolation are all covered without launching a browser. One real render
is included behind ``STEPPY_PDF_E2E=1``: a mock can prove we *called*
``page.pdf`` correctly but not that Chromium produces a PDF with the
screenshots in it.
"""

import os
from pathlib import Path

import pytest

from steppy.models import PdfConfig
from steppy.pdf import PdfJob, PdfRenderError, pdf_options, render_pdfs


# ── Fake playwright ──────────────────────────────────────────────────────────


class Recorder:
    def __init__(self):
        self.launches = 0
        self.pages = 0
        self.gotos: list[tuple] = []
        self.evaluated: list[str] = []
        self.waits: list[tuple] = []
        self.pdfs: list[dict] = []
        self.closed = 0


class FakePage:
    def __init__(self, rec: Recorder, *, pdf_error: str = "", wait_error: bool = False):
        self._rec = rec
        self._pdf_error = pdf_error
        self._wait_error = wait_error

    def goto(self, url, **kwargs):
        self._rec.gotos.append((url, kwargs))

    def evaluate(self, script):
        self._rec.evaluated.append(script)

    def wait_for_function(self, script, timeout=None):
        self._rec.waits.append((script, timeout))
        if self._wait_error:
            raise RuntimeError("timeout waiting for images")

    def pdf(self, **kwargs):
        self._rec.pdfs.append(kwargs)
        if self._pdf_error and self._pdf_error in str(kwargs.get("path", "")):
            raise RuntimeError("boom")
        Path(kwargs["path"]).write_bytes(b"%PDF-1.4 fake\n")


class FakeBrowser:
    def __init__(self, rec: Recorder, **page_kwargs):
        self._rec = rec
        self._page_kwargs = page_kwargs

    def new_page(self):
        self._rec.pages += 1
        return FakePage(self._rec, **self._page_kwargs)

    def close(self):
        self._rec.closed += 1


class FakeChromium:
    def __init__(self, rec: Recorder, *, launch_error: bool = False, **page_kwargs):
        self._rec = rec
        self._launch_error = launch_error
        self._page_kwargs = page_kwargs

    def launch(self, **kwargs):
        self._rec.launches += 1
        if self._launch_error:
            raise RuntimeError("Executable doesn't exist at ...chrome.exe")
        return FakeBrowser(self._rec, **self._page_kwargs)


class FakePlaywrightCtx:
    def __init__(self, rec: Recorder, **kwargs):
        self.chromium = FakeChromium(rec, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_playwright(monkeypatch):
    """Install a fake ``sync_playwright`` and hand back the recorder.

    ``render_pdfs`` imports it lazily inside the function body, so patching the
    attribute on the real module is enough.
    """
    import playwright.sync_api as sync_api

    def install(**kwargs) -> Recorder:
        rec = Recorder()
        monkeypatch.setattr(sync_api, "sync_playwright", lambda: FakePlaywrightCtx(rec, **kwargs))
        return rec

    return install


def _jobs(tmp_path, n=2) -> list[PdfJob]:
    jobs = []
    for i in range(n):
        html = tmp_path / f"guide-{i}.html"
        html.write_text("<html><body>x</body></html>", encoding="utf-8")
        jobs.append(PdfJob(html=html, pdf=tmp_path / f"guide-{i}.pdf", title=f"Guide {i}"))
    return jobs


# ── Batching ─────────────────────────────────────────────────────────────────


def test_one_browser_and_page_for_the_whole_batch(fake_playwright, tmp_path):
    """A guide project is scenarios x locales x layouts — a launch per file
    would dominate the runtime."""
    rec = fake_playwright()
    results = render_pdfs(_jobs(tmp_path, 5), PdfConfig())

    assert [r.ok for r in results] == [True] * 5
    assert rec.launches == 1
    assert rec.pages == 1
    assert rec.closed == 1


def test_empty_batch_never_launches_a_browser(fake_playwright):
    rec = fake_playwright()
    assert render_pdfs([], PdfConfig()) == []
    assert rec.launches == 0


def test_on_done_streams_progress(fake_playwright, tmp_path):
    fake_playwright()
    seen = []
    render_pdfs(_jobs(tmp_path, 3), PdfConfig(), on_done=seen.append)
    assert [r.job.pdf.name for r in seen] == ["guide-0.pdf", "guide-1.pdf", "guide-2.pdf"]


# ── Page loading ─────────────────────────────────────────────────────────────


def test_pages_load_over_file_url(fake_playwright, tmp_path):
    """Guide HTML references screenshots with relative URLs — set_content would
    resolve them against about:blank and every image would be blank."""
    rec = fake_playwright()
    jobs = _jobs(tmp_path, 1)
    render_pdfs(jobs, PdfConfig())

    url, kwargs = rec.gotos[0]
    assert url == jobs[0].html.resolve().as_uri()
    assert url.startswith("file://")
    assert kwargs["wait_until"] == "load"


def test_lazy_images_are_forced_eager_and_awaited(fake_playwright, tmp_path):
    """Both guide templates emit loading="lazy"; unloaded below-the-fold images
    are the classic blank-stage print bug."""
    rec = fake_playwright()
    render_pdfs(_jobs(tmp_path, 1), PdfConfig(timeout=4321))

    assert "loading = 'eager'" in rec.evaluated[0]
    script, timeout = rec.waits[0]
    assert "img.complete" in script
    assert timeout == 4321


def test_image_wait_timeout_still_produces_a_pdf(fake_playwright, tmp_path):
    """A PDF with one missing screenshot beats no PDF — but say so."""
    fake_playwright(wait_error=True)
    warnings = []
    results = render_pdfs(_jobs(tmp_path, 1), PdfConfig(), on_warning=warnings.append)

    assert results[0].ok
    assert results[0].job.pdf.exists()
    assert warnings and "screenshots" in warnings[0]


# ── Option mapping ───────────────────────────────────────────────────────────


def test_pdf_options_maps_every_config_field():
    opts = pdf_options(
        PdfConfig(format="Letter", landscape=True, scale=0.8, print_background=False),
        "My Guide",
    )
    assert opts["format"] == "Letter"
    assert opts["landscape"] is True
    assert opts["scale"] == 0.8
    assert opts["print_background"] is False
    assert opts["margin"] == {"top": "12mm", "right": "12mm", "bottom": "14mm", "left": "12mm"}


def test_page_numbers_render_a_footer_with_the_title():
    opts = pdf_options(PdfConfig(page_numbers=True), "Créer un dossier")
    assert opts["display_header_footer"] is True
    # An empty header template is required, else Chromium prints its own
    # date + document title.
    assert opts["header_template"] == "<span></span>"
    assert 'class="pageNumber"' in opts["footer_template"]
    assert 'class="totalPages"' in opts["footer_template"]
    assert "Créer un dossier" in opts["footer_template"]


def test_footer_title_is_html_escaped():
    opts = pdf_options(PdfConfig(), '<script>alert("x")</script>')
    assert "<script>" not in opts["footer_template"]
    assert "&lt;script&gt;" in opts["footer_template"]


def test_page_numbers_off_emits_no_header_footer():
    opts = pdf_options(PdfConfig(page_numbers=False), "t")
    assert "display_header_footer" not in opts
    assert "footer_template" not in opts


def test_options_reach_page_pdf(fake_playwright, tmp_path):
    rec = fake_playwright()
    render_pdfs(_jobs(tmp_path, 1), PdfConfig(format="A5", landscape=True))

    assert rec.pdfs[0]["format"] == "A5"
    assert rec.pdfs[0]["landscape"] is True
    assert rec.pdfs[0]["path"].endswith("guide-0.pdf")


def test_default_config_prints_backgrounds():
    """Playwright defaults print_background to False, which would drop the step
    badges and the role chip — the guide's whole visual identity."""
    assert pdf_options(PdfConfig())["print_background"] is True


# ── Failure isolation ────────────────────────────────────────────────────────


def test_one_bad_page_does_not_lose_the_batch(fake_playwright, tmp_path):
    fake_playwright(pdf_error="guide-1.pdf")
    results = render_pdfs(_jobs(tmp_path, 3), PdfConfig())

    assert [r.ok for r in results] == [True, False, True]
    assert "boom" in results[1].error


def test_missing_source_html_is_reported_not_raised(fake_playwright, tmp_path):
    rec = fake_playwright()
    job = PdfJob(html=tmp_path / "nope.html", pdf=tmp_path / "nope.pdf")
    (result,) = render_pdfs([job], PdfConfig())

    assert not result.ok
    assert "not found" in result.error
    assert rec.pdfs == []


def test_launch_failure_raises_with_an_actionable_hint(fake_playwright, tmp_path):
    fake_playwright(launch_error=True)
    with pytest.raises(PdfRenderError) as exc:
        render_pdfs(_jobs(tmp_path, 1), PdfConfig())

    assert "playwright install chromium" in str(exc.value)


def test_output_directory_is_created(fake_playwright, tmp_path):
    fake_playwright()
    html = tmp_path / "g.html"
    html.write_text("<html></html>", encoding="utf-8")
    target = tmp_path / "deep" / "nested" / "g.pdf"
    (result,) = render_pdfs([PdfJob(html=html, pdf=target)], PdfConfig())

    assert result.ok
    assert target.exists()


# ── Real render (opt-in) ─────────────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("STEPPY_PDF_E2E", "") in ("", "0"),
    reason="set STEPPY_PDF_E2E=1 to run the real headless-Chromium render",
)
def test_real_render_embeds_a_lazy_image(tmp_path):
    """The mocks can't prove Chromium produces a real PDF — or that the
    eager-image fix works. This does both."""
    png = tmp_path / "shot.png"
    # 1x1 red PNG.
    png.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000d4944415478da63f8cfc0000003010100f3fcffdd00"
            "00000049454e44ae426082"
        )
    )
    html = tmp_path / "guide.html"
    html.write_text(
        "<html><body><div style='height:2000px'>spacer</div>"
        f"<img src='{png.name}' loading='lazy' width='400'></body></html>",
        encoding="utf-8",
    )
    out = tmp_path / "guide.pdf"
    (result,) = render_pdfs([PdfJob(html=html, pdf=out, title="Real")], PdfConfig())

    assert result.ok, result.error
    data = out.read_bytes()
    assert data.startswith(b"%PDF-")
    # The image survived into the document — i.e. the lazy image was loaded
    # before printing.
    assert b"/Subtype /Image" in data
