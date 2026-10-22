/**
 * Inline-markdown renderer shared by the guide player and the in-app tour
 * overlay.
 *
 * Mirrors the inline subset of the server-side ``markdown-it`` ``renderInline``
 * filter (``src/steppy/guide.py``, configured ``{html:false, breaks:true,
 * linkify:true}``) used by the step-by-step guide, so the interactive player
 * renders the same content the static guide does:
 *
 *   - **bold** / __bold__ , *italic* / _italic_ (recursive — emphasis nests)
 *   - `inline code`
 *   - [label](url) links — label is parsed recursively
 *   - autolinked bare URLs (``https://…`` / ``www.…``) and emails (linkify)
 *   - hard line breaks: every ``\n`` becomes ``<br>`` (``breaks:true``)
 *
 * The manifest carries raw text (it is reused for ``alt``/``aria-label``
 * attributes, where HTML must not appear), so emphasis is rendered here, on the
 * client, only at the visible display sites.
 *
 * Returns an array of literal-string + Lit ``TemplateResult`` parts, meant to be
 * interpolated into an existing parent element (``${renderInlineMarkdown(text)}``,
 * the same shape as ``${items.map(...)}``). Literal text and ``href`` values stay
 * auto-escaped by Lit, and which link URLs become links is decided by a
 * ``LinkPolicy`` — so it is XSS-safe by construction, with no ``unsafeHTML``.
 *
 * Link policies: the guide keeps its historical set (http/https/mailto, a
 * site-relative path, an anchor) minus protocol-relative ``//host`` and
 * ``/\host``, which browsers resolve to another origin. The tour overlay passes
 * a stricter policy of its own (same-origin routes and the guide origin only,
 * no autolinks). A URL the policy refuses leaves the whole ``[label](url)``
 * literal, exactly as an unmatched link always rendered.
 *
 * Scope is intentionally inline-only (no block constructs — ``renderInline`` does
 * not render lists/headings/blockquotes either, so there is no parity gap). The
 * regex emphasis rule is not markdown-it's full delimiter-run algorithm, so a few
 * pathological mixed-nesting cases (e.g. *italic wrapping **bold***) are not
 * matched; the common cases — bold-containing-italic, adjacent emphasis, links
 * with emphasised labels — all work.
 */

import { html, type TemplateResult } from 'lit';

export type MarkdownNode = TemplateResult | string;

/** What a ``[label](url)`` becomes: a new-tab link, or a same-tab one the host routes. */
export type LinkTarget =
  | { kind: 'external'; href: string }
  | { kind: 'internal'; href: string; onActivate: (event: MouseEvent) => void };

export interface LinkPolicy {
  /** The link for ``url``, or ``null`` to leave the markdown literal. */
  link(url: string): LinkTarget | null;
  /** Whether bare URLs and e-mail addresses are turned into links (linkify). */
  autolink: boolean;
}

// The guide's historical link set, minus protocol-relative "//host" and "/\host".
const SAFE_GUIDE_URL = /^(?:https?:\/\/[^\s)]+|mailto:[^\s)]+|\/(?![/\\])[^\s)]*|#[^\s)]*)$/;

export const GUIDE_LINK_POLICY: LinkPolicy = {
  autolink: true,
  link: (url) => (SAFE_GUIDE_URL.test(url) ? { kind: 'external', href: url } : null),
};

// 1 code | 2 link-label 3 link-url | 4 bold-delim 5 bold | 6 em-delim 7 em | 8 url/www | 9 email
// The link URL is any run up to ")" without whitespace; the policy decides
// whether it becomes a link.
const INLINE_SRC =
  '`([^`]+)`|\\[([^\\]]+)\\]\\(([^\\s)]+)\\)|(\\*\\*|__)(\\S(?:.*?\\S)?)\\4|(\\*|_)([^*_\\s](?:[^*_]*?[^*_\\s])?)\\6|(https?:\\/\\/[^\\s<>()]+|www\\.[^\\s<>()]+)|([\\w.+-]+@[\\w-]+\\.[\\w.-]+\\w)';

export function renderInlineMarkdown(src: string, policy: LinkPolicy = GUIDE_LINK_POLICY): MarkdownNode[] {
  const parts: MarkdownNode[] = [];
  // Hard line breaks: breaks:true turns every \n into <br>.
  const pushText = (t: string) =>
    t.split('\n').forEach((ln, i) => {
      if (i > 0) parts.push(html`<br />`);
      if (ln) parts.push(ln);
    });

  // A fresh regex per call: this function recurses (into emphasis/link content),
  // and a single shared /g regex would have its lastIndex clobbered by the inner
  // call, corrupting this loop. A local instance keeps each level independent.
  const re = new RegExp(INLINE_SRC, 'g');
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    if (m[2] != null) {
      const target = policy.link(m[3]!);
      if (!target) {
        // Refused: no match at this position, exactly as if the link pattern
        // had not matched — scanning resumes one character later.
        re.lastIndex = m.index + 1;
        continue;
      }
      if (m.index > last) pushText(src.slice(last, m.index));
      parts.push(
        target.kind === 'external'
          ? html`<a href=${target.href} target="_blank" rel="noopener noreferrer"
              >${renderInlineMarkdown(m[2], policy)}</a
            >`
          : html`<a href=${target.href} @click=${target.onActivate}>${renderInlineMarkdown(m[2], policy)}</a>`,
      );
      last = m.index + m[0].length;
      continue;
    }
    if ((m[8] != null || m[9] != null) && !policy.autolink) continue; // stays literal text
    if (m.index > last) pushText(src.slice(last, m.index));
    if (m[1] != null) {
      parts.push(html`<code>${m[1]}</code>`); // code span: no recursion
    } else if (m[4] != null) {
      parts.push(html`<strong>${renderInlineMarkdown(m[5]!, policy)}</strong>`);
    } else if (m[6] != null) {
      parts.push(html`<em>${renderInlineMarkdown(m[7]!, policy)}</em>`);
    } else if (m[8] != null) {
      // Autolink; trim trailing sentence punctuation back out as text (linkify behavior).
      const raw = m[8];
      const trail = (raw.match(/[.,;:!?]+$/) ?? [''])[0];
      const url = trail ? raw.slice(0, -trail.length) : raw;
      const href = url.startsWith('www.') ? `https://${url}` : url;
      parts.push(html`<a href=${href} target="_blank" rel="noopener noreferrer">${url}</a>`);
      if (trail) pushText(trail);
    } else if (m[9] != null) {
      parts.push(html`<a href=${`mailto:${m[9]}`}>${m[9]}</a>`);
    }
    last = m.index + m[0].length;
  }
  if (last < src.length) pushText(src.slice(last));
  return parts;
}
