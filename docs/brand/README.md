# Steppy brand

The mark is a checked box: two square brackets holding a check. The brackets
read as code and as a checklist item; the check is the one orange element.

| File | Use |
|---|---|
| `steppy-mark.svg` / `steppy-mark-dark.svg` | Mark alone, square |
| `steppy-icon.svg` | App icon and favicon: the mark on a Pin tile, with heavier strokes for small sizes |

There is no wordmark: next to the mark, set the name in text. Write it
`Steppy` in prose.

## Palette

| Name | Hex | Role |
|---|---|---|
| Pin | `#15332A` | Text, the brackets, dark grounds |
| Lichen | `#E4E9E1` | Light ground |
| Balise | `#D2561A` | The check, step markers — graphics only |
| Granite | `#5B6B64` | Secondary text |

Balise is 4.0:1 on Lichen, under the 4.5:1 that small text needs, so it is
never used for text: in the run report it is the focus ring, the page you are on,
the step a link names and the target on a screenshot (`#F09A6A` on dark grounds,
where Balise itself is too faint). Links are Pin ink with an underline. Passed,
failed and flaky have colours of their own, always with an icon and a word, so
orange is never read as a status.

## Type

Bricolage Grotesque for headings and text, JetBrains Mono for code. The run
report carries both as files (`templates/report/fonts/`, SIL Open Font License).

The generated guide sites keep their neutral styling on purpose: they
document the user's application, not Steppy.
