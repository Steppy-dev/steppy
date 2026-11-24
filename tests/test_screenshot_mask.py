"""Tests for FLAWS §3.5 — screenshot redaction.

`screenshot.mask` (CSS selectors) + `mask_color` drive Playwright's native
`page.screenshot(mask=[...], mask_color=...)` so secrets and personal data can be boxed out of
captured guides. No masks configured → no mask kwargs (byte-identical output).
The masks of a scenario and of a step are in ``tests/test_masks.py``.
"""

from pathlib import Path

from steppy.models import ScreenshotConfig

_CONFTEST = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"


# ── model ────────────────────────────────────────────────────────────────────


def test_screenshot_config_defaults():
    sc = ScreenshotConfig()
    assert sc.mask == []
    assert sc.mask_color == "#000000"


def test_screenshot_config_accepts_mask():
    sc = ScreenshotConfig(mask=["input[name='password']", ".pii"], mask_color="#123456")
    assert sc.mask == ["input[name='password']", ".pii"]
    assert sc.mask_color == "#123456"


# ── _screenshot_mask_kwargs helper ───────────────────────────────────────────


class _FakeLocator:
    def __init__(self, sel):
        self.sel = sel


class _FakePage:
    def locator(self, sel):
        return _FakeLocator(sel)


def _load_helper(config: dict, step_mask=()):
    text = _CONFTEST.read_text(encoding="utf-8")
    start = text.index("def _screenshot_mask_kwargs(")
    end = text.index("def take_screenshot(", start)
    ns = {"CONFIG": config, "Page": object, "_STEP_MASK": list(step_mask)}
    exec(compile(text[start:end], "<mask>", "exec"), ns)
    return ns["_screenshot_mask_kwargs"]


def test_mask_kwargs_empty_when_unconfigured():
    fn = _load_helper({"screenshot": {}})
    assert fn(_FakePage()) == {}


def test_mask_kwargs_empty_when_mask_missing_entirely():
    fn = _load_helper({})
    assert fn(_FakePage()) == {}


def test_mask_kwargs_builds_locators_and_color():
    fn = _load_helper({"screenshot": {"mask": ["#a", ".b"], "mask_color": "#fff"}})
    kwargs = fn(_FakePage())
    assert set(kwargs.keys()) == {"mask", "mask_color"}
    assert kwargs["mask_color"] == "#fff"
    assert [loc.sel for loc in kwargs["mask"]] == ["#a", ".b"]


def test_mask_kwargs_default_color():
    fn = _load_helper({"screenshot": {"mask": ["#a"]}})
    assert fn(_FakePage())["mask_color"] == "#000000"


def test_mask_kwargs_add_the_running_steps_masks():
    """A scenario's and a step's masks add to the project's, never replace them."""
    fn = _load_helper({"screenshot": {"mask": ["#a", ".b"]}}, step_mask=[".b", ".c"])
    assert [loc.sel for loc in fn(_FakePage())["mask"]] == ["#a", ".b", ".c"]


def test_mask_kwargs_take_explicit_masks_instead_of_the_steps():
    """The failure capture passes the scenario's whole union."""
    fn = _load_helper({"screenshot": {"mask": ["#a"]}}, step_mask=[".step"])
    assert [loc.sel for loc in fn(_FakePage(), [".d"])["mask"]] == ["#a", ".d"]
    assert [loc.sel for loc in fn(_FakePage(), ())["mask"]] == ["#a"]


def test_mask_kwargs_with_only_a_step_mask():
    fn = _load_helper({"screenshot": None}, step_mask=[".c"])
    assert [loc.sel for loc in fn(_FakePage())["mask"]] == [".c"]
    assert fn(_FakePage())["mask_color"] == "#000000"
