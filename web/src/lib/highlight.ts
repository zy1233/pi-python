/**
 * A small syntax highlighter for the code blocks of the site.
 * It runs when the site is built, so the browser gets plain HTML spans.
 * It knows three languages. Any other text stays plain.
 */
export type Lang = 'bash' | 'json' | 'python' | 'text';

const escapeHtml = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// Each grammar is one regular expression. The name of the group that matches
// becomes the CSS class `tok-<name>`. Only the first alternative that matches at a
// position counts, so the order of the parts matters.
const grammar = (parts: string[]) => new RegExp(parts.join('|'), 'gm');

const grammars: Record<Exclude<Lang, 'text'>, RegExp> = {
  bash: grammar([
    String.raw`(?<comment>(?<=^|\s)#[^\n]*)`,
    String.raw`(?<string>"(?:[^"\\\n]|\\.)*"|'[^'\n]*')`,
    String.raw`(?<var>\$\{?[A-Za-z_]\w*\}?)`,
    String.raw`(?<flag>(?<=\s)--?[A-Za-z][\w-]*)`,
    String.raw`(?<cmd>(?<=^[ \t]*|(?:&&|\|\||[|;])[ \t]*)[A-Za-z][\w./-]*)`,
  ]),
  json: grammar([
    String.raw`(?<key>"(?:[^"\\\n]|\\.)*"(?=\s*:))`,
    String.raw`(?<string>"(?:[^"\\\n]|\\.)*")`,
    String.raw`(?<lit>\b(?:true|false|null)\b|-?\b\d+(?:\.\d+)?\b)`,
    String.raw`(?<punct>[{}\[\],:])`,
  ]),
  python: grammar([
    String.raw`(?<comment>#[^\n]*)`,
    String.raw`(?<string>[fFrRbB]{0,2}(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'))`,
    String.raw`(?<kw>\b(?:from|import|as|async|await|def|class|return|if|else|elif|for|in|while|try|except|finally|with|lambda|pass|raise|yield|not|and|or|is|None|True|False)\b)`,
    String.raw`(?<num>\b\d[\d_]*(?:\.\d+)?\b)`,
    String.raw`(?<fn>\b[A-Za-z_]\w*(?=\())`,
  ]),
};

export function highlight(code: string, lang: Lang): string {
  if (lang === 'text') return escapeHtml(code);
  let html = '';
  let last = 0;
  for (const match of code.matchAll(grammars[lang])) {
    const start = match.index ?? 0;
    const kind = Object.entries(match.groups ?? {}).find(([, value]) => value !== undefined)?.[0];
    html += escapeHtml(code.slice(last, start));
    html += kind ? `<span class="tok-${kind}">${escapeHtml(match[0])}</span>` : escapeHtml(match[0]);
    last = start + match[0].length;
  }
  return html + escapeHtml(code.slice(last));
}
