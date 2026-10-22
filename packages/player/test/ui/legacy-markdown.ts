/**
 * Frozen copy of ``src/markdown.ts`` as it was before the ``src/ui/`` extraction.
 * Test-only oracle: ``guide-markdown-parity.test.ts`` renders every input with
 * both and requires the same DOM (except the ``//host`` links the shared
 * renderer now refuses). Never import it from ``src/``.
 */

import { html, type TemplateResult } from 'lit';

type Node = TemplateResult | string;

// 1 code | 2 link-label 3 link-url | 4 bold-delim 5 bold | 6 em-delim 7 em | 8 url/www | 9 email
// The link URL is restricted to safe schemes (http/https/mailto) + relative (/…) +
// anchor (#…); anything else (e.g. javascript:) simply doesn't match and stays literal.
const INLINE_SRC =
  '`([^`]+)`|\\[([^\\]]+)\\]\\((https?:\\/\\/[^\\s)]+|mailto:[^\\s)]+|\\/[^\\s)]*|#[^\\s)]*)\\)|(\\*\\*|__)(\\S(?:.*?\\S)?)\\4|(\\*|_)([^*_\\s](?:[^*_]*?[^*_\\s])?)\\6|(https?:\\/\\/[^\\s<>()]+|www\\.[^\\s<>()]+)|([\\w.+-]+@[\\w-]+\\.[\\w.-]+\\w)';

export function legacyRenderInlineMarkdown(src: string): Node[] {
  const parts: Node[] = [];
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
    if (m.index > last) pushText(src.slice(last, m.index));
    if (m[1] != null) {
      parts.push(html`<code>${m[1]}</code>`); // code span: no recursion
    } else if (m[2] != null) {
      parts.push(
        html`<a href=${m[3]} target="_blank" rel="noopener noreferrer"
          >${legacyRenderInlineMarkdown(m[2])}</a
        >`,
      );
    } else if (m[4] != null) {
      parts.push(html`<strong>${legacyRenderInlineMarkdown(m[5]!)}</strong>`);
    } else if (m[6] != null) {
      parts.push(html`<em>${legacyRenderInlineMarkdown(m[7]!)}</em>`);
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
