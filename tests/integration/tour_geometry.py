"""Where the tour overlay draws, measured in a real browser.

Shared by the browser suites (``test_tour_player_it.py``, ``test_tour_angular_it.py``).
happy-dom has no layout, so the overlay's unit tests feed it made-up rectangles;
these helpers read the real ones — the target's box, the spotlight's, the
bubble's, the app's open dialogs — and what a click at a point would hit.

``points_at`` waits until the overlay visibly points at a test-id: the
spotlight frames it (and no more than the overlay's padding around it), the
bubble is on screen and off the target's centre, and a click at that centre
reaches the target. ``clear_of_dialogs`` waits until the bubble covers no open
app dialog, and every button in them can be clicked.
"""

import time

#: ``SPOT_PAD`` in ``packages/player/src/tour/tracker.ts``.
SPOT_PAD = 6
SLACK = 1.5
PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True, "device_scale_factor": 3}
DESKTOP = {"viewport": {"width": 1280, "height": 800}}
SHEETS = ("sheet-top", "sheet-bottom")

_MEASURE = """
(id) => {
  const box = (el) => {
    if (!el || !el.isConnected) return null;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 ? {x: r.left, y: r.top, w: r.width, h: r.height} : null;
  };
  const host = document.querySelector('steppy-tour-host');
  const root = host && host.shadowRoot;
  const bubble = root && root.querySelector('[part~="bubble"]');
  const spot = root && root.querySelector('[part~="spotlight"]');
  const shown = (el) => el && getComputedStyle(el).display !== 'none' ? box(el) : null;
  const landsIn = (el) => {
    const b = box(el);
    if (!b) return false;
    const hit = document.elementFromPoint(b.x + b.w / 2, b.y + b.h / 2);
    return !!hit && (hit === el || el.contains(hit));
  };
  const target = id ? document.querySelector(`[data-testid="${id}"]`) : null;
  const dialogs = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')]
    .map((d) => ({box: box(d), buttons: [...d.querySelectorAll('button')].filter(box).map((b) => ({
      label: b.textContent.trim(), reachable: landsIn(b)}))}))
    .filter((d) => d.box);
  return {
    vw: innerWidth, vh: innerHeight,
    stepId: bubble && bubble.getAttribute('data-step-id'),
    placed: !!bubble && bubble.hasAttribute('data-placed'),
    mode: bubble && bubble.getAttribute('data-mode'),
    bubble: box(bubble), spot: shown(spot), target: box(target),
    reachable: target ? landsIn(target) : null,
    dialogs,
  };
}
"""


def measure(page, test_id=None) -> dict:
    return page.evaluate(_MEASURE, test_id)


def _inside(inner, outer, slack=SLACK) -> bool:
    return (
        inner["x"] >= outer["x"] - slack
        and inner["y"] >= outer["y"] - slack
        and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + slack
        and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + slack
    )


def _overlaps(a, b) -> bool:
    return (
        a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]
    )


def _contains_point(r, x, y) -> bool:
    return r["x"] <= x <= r["x"] + r["w"] and r["y"] <= y <= r["y"] + r["h"]


def _clip(r, g):
    x, y = max(r["x"], 0), max(r["y"], 0)
    right, bottom = min(r["x"] + r["w"], g["vw"]), min(r["y"] + r["h"], g["vh"])
    return {"x": x, "y": y, "w": right - x, "h": bottom - y} if right > x and bottom > y else None


def pointing_problems(g: dict, test_id: str, modes=None) -> list[str]:
    """What keeps the overlay from visibly pointing at ``test_id`` (empty: it does)."""
    screen = {"x": 0, "y": 0, "w": g["vw"], "h": g["vh"]}
    target, spot, bubble = g["target"], g["spot"], g["bubble"]
    problems = []
    if g["stepId"] is None or not g["placed"]:
        return ["no placed bubble"]
    if modes and g["mode"] not in modes:
        problems.append(f"bubble mode {g['mode']!r}, expected one of {modes}")
    if bubble is None or not _inside(bubble, screen):
        problems.append("the bubble is not entirely on screen")
    if target is None:
        return [*problems, "the target has no box"]
    cx, cy = target["x"] + target["w"] / 2, target["y"] + target["h"] / 2
    if not _contains_point(screen, cx, cy):
        problems.append("the target's centre is off screen")
    visible = _clip(target, g)
    if spot is None:
        problems.append("no spotlight is drawn")
    elif visible is not None:
        if not _inside(visible, spot):
            problems.append("the spotlight does not frame the target")
        pad = 2 * SPOT_PAD + 2 * SLACK
        if spot["w"] > visible["w"] + pad or spot["h"] > visible["h"] + pad:
            problems.append("the spotlight is larger than the target and its padding")
    if bubble is not None and _contains_point(bubble, cx, cy):
        problems.append("the bubble covers the target's centre")
    if not g["reachable"]:
        problems.append("a click at the target's centre does not reach it")
    return problems


def _wait(page, check, what, timeout_ms):
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        g, problems = check()
        if not problems:
            return g
        if time.monotonic() > deadline:
            raise AssertionError(f"{what}: {'; '.join(problems)}\n  measured: {g}")
        page.wait_for_timeout(50)


def points_at(page, test_id: str, *, modes=None, timeout_ms=8000) -> dict:
    """Wait until the shown step visibly points at ``test_id``; the last measurement."""

    def check():
        g = measure(page, test_id)
        problems = pointing_problems(g, test_id, modes)
        if g["stepId"] != test_id:
            problems.insert(0, f"the bubble shows step {g['stepId']!r}")
        return g, problems

    return _wait(page, check, f"the overlay does not point at {test_id!r}", timeout_ms)


def clear_of_dialogs(page, *, modes=None, timeout_ms=8000) -> dict:
    """Wait until a placed bubble covers no open app dialog and every dialog button can be clicked."""

    def check():
        g = measure(page)
        problems = [] if g["placed"] else ["no placed bubble"]
        if not g["dialogs"]:
            problems.append("no app dialog is open")
        if modes and g["mode"] not in modes:
            problems.append(f"bubble mode {g['mode']!r}, expected one of {modes}")
        for dialog in g["dialogs"]:
            if g["bubble"] and _overlaps(g["bubble"], dialog["box"]):
                problems.append(f"the bubble covers the dialog at {dialog['box']}")
            problems += [
                f"the dialog's {b['label']!r} button is covered" for b in dialog["buttons"] if not b["reachable"]
            ]
        return g, problems

    return _wait(page, check, "the bubble is in the way of an open dialog", timeout_ms)
