"""French colons (``Segment : …``) no longer need quoting.

A plain YAML scalar can neither contain ``": "`` nor end with ``:``, so
``title: Segment : choix`` in the frontmatter, or under ``guide-text:``, failed
with "mapping values are not allowed here". Top-level ``- key: value`` step lines
never had the problem (the parser splits them on the first colon). The repair is
a retry: valid YAML is parsed exactly as before.
"""

from pathlib import Path
from textwrap import dedent

import pytest

from steppy.parser import ParseError, _quote_colon_scalars, parse_scenario_file


def _write(tmp_path: Path, content: str, newline: str = "\n") -> Path:
    path = tmp_path / "scenario.md"
    path.write_bytes(dedent(content).lstrip("\n").replace("\n", newline).encode("utf-8"))
    return path


_STEP = """
### 1. Ouvrir les factures
- role: editor
- action: navigate
- target: /invoices
"""


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_frontmatter_values_with_a_french_colon(tmp_path, newline):
    path = _write(
        tmp_path,
        dedent(
            """
        ---
        title: Segment : factures
        description: Étape : créer puis envoyer
        tags: [user-guide-editor]
        ---
        """
        )
        + _STEP,
        newline,
    )
    scenario = parse_scenario_file(path)
    assert scenario.title == "Segment : factures"
    assert scenario.description == "Étape : créer puis envoyer"
    assert scenario.tags == ["user-guide-editor"]
    assert scenario.steps[0].target == "/invoices"


def test_structured_guide_text_with_a_french_colon(tmp_path):
    path = _write(
        tmp_path,
        """
        ---
        title: Factures
        ---

        ### 1. Ouvrir les factures
        - role: editor
        - action: navigate
        - target: /invoices
        - guide-text:
            fr:
              title: Segment : la liste
              subtitle: Remarque :
              text: "Déjà : entre guillemets"
            en:
              title: The list
        """,
    )
    fr = parse_scenario_file(path).steps[0].guide_text.fr
    assert fr.title == "Segment : la liste"
    assert fr.subtitle == "Remarque :"
    assert fr.text == "Déjà : entre guillemets"


def test_simple_guide_text_with_a_french_colon(tmp_path):
    path = _write(
        tmp_path,
        """
        ---
        title: Factures
        ---

        ### 1. Ouvrir les factures
        - role: editor
        - action: navigate
        - target: /invoices
        - guide-text:
            fr: Segment : ouvrez la liste des factures.
            en: Open the invoice list.
        """,
    )
    assert parse_scenario_file(path).steps[0].guide_text.fr.text == "Segment : ouvrez la liste des factures."


def test_broken_yaml_still_reports_the_original_error(tmp_path):
    path = _write(
        tmp_path,
        """
        ---
        title: Factures
        ---

        ### 1. Ouvrir les factures
        - role: editor
        - action: navigate
        - target: /invoices
        - guide-text:
            fr: [non fermé
        """,
    )
    with pytest.raises(ParseError, match="Failed to parse nested content for 'guide-text'"):
        parse_scenario_file(path)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("title: Segment : choix", 'title: "Segment : choix"'),
        ("  - title: Note:", '  - title: "Note:"'),
        ('title: "Segment : choix"', 'title: "Segment : choix"'),
        ("title: [a, b]", "title: [a, b]"),
        ("url: http://app.test/x", "url: http://app.test/x"),
        ("title: Segment : x # a comment", "title: Segment : x # a comment"),
        ("Une phrase : sans clé", "Une phrase : sans clé"),
    ],
)
def test_only_the_offending_values_are_quoted(line, expected):
    assert _quote_colon_scalars(line) == expected


# ── expect: lists ───────────────────────────────────────────────────────────
#
# `- toast("Segment : créé")` is valid YAML: a one-key mapping whose key is
# `toast("Segment`. Nothing failed to parse, so the retry above never ran and
# the step was rejected later as "Input should be a valid string".


def _expect_step(tmp_path: Path, expect_block: str, newline: str = "\n") -> list:
    header = dedent(
        """
        ---
        title: Factures
        ---

        ### 1. Enregistrer
        - role: editor
        - action: click
        - selector: '[data-testid="save"]'
        - expect:
        """
    )
    block = "".join("    " + ln for ln in dedent(expect_block).splitlines(keepends=True))
    path = _write(tmp_path, header + block, newline)
    return parse_scenario_file(path).steps[0].expect


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_list_items_with_a_french_colon(tmp_path, newline):
    expect = _expect_step(
        tmp_path,
        """\
            - toast("Segment : créé")
            - toast("Étape : a : b")
            - contains([data-testid="total"], "Total : 12")
            - visible([data-testid="saved"])
        """,
        newline,
    )
    assert expect == [
        'toast("Segment : créé")',
        'toast("Étape : a : b")',
        'contains([data-testid="total"], "Total : 12")',
        'visible([data-testid="saved"])',
    ]


def test_dict_entries_with_a_french_colon(tmp_path):
    (entry,) = _expect_step(
        tmp_path,
        """\
            - message: Total : faux
              check: contains(#t, "Total : 12")
        """,
    )
    assert entry.check == 'contains(#t, "Total : 12")'
    assert entry.message == "Total : faux"


def test_quoted_items_are_read_as_written(tmp_path):
    expect = _expect_step(
        tmp_path,
        """\
            - 'toast("Segment : créé")'
            - "visible(#a)"
        """,
    )
    assert expect == ['toast("Segment : créé")', "visible(#a)"]


def test_inline_expect_list_with_a_french_colon(tmp_path):
    path = _write(
        tmp_path,
        """
        ---
        title: Factures
        ---

        ### 1. Enregistrer
        - role: editor
        - action: click
        - selector: '[data-testid="save"]'
        - expect: [toast("Segment : créé"), visible([data-testid="a,b"])]
        """,
    )
    assert parse_scenario_file(path).steps[0].expect == ['toast("Segment : créé")', 'visible([data-testid="a,b"])']
