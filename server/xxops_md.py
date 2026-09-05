"""Markdown rendering for the docs pages.

Lifted out of xxops-server.py unchanged. Text in, HTML out - it knows nothing
about validators, sessions or requests.

docs_dir() deliberately stayed in the server: where the files live depends on
the installation, which is not this module's business.
"""

import html
import re

DOC_NAME = re.compile(r"[a-z0-9._-]+\.md")


def md_escape(t):
    # The quote matters as much as the brackets: md_inline drops a captured
    # URL into href="...", so an unescaped quote there closes the attribute
    # and opens another one.
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def md_inline(t):
    """bold, italic, inline code and links - applied after escaping."""
    t = md_escape(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               r'<a href="\2" rel="noopener" target="_blank">\1</a>', t)
    return t


def md_to_html(src):
    """Enough markdown for the documents this project writes, and no more.

    Anything unrecognised falls through as a paragraph rather than breaking
    the page - a doc that renders imperfectly beats one that renders blank.
    """
    out, lines, i = [], src.replace("\r\n", "\n").split("\n"), 0
    while i < len(lines):
        ln = lines[i]

        if ln.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(md_escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            continue

        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", ln):
            out.append("<hr>")
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, md_inline(m.group(2)), lvl))
            i += 1
            continue

        if "|" in ln and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]):
            def cells(row):
                return [c.strip() for c in row.strip().strip("|").split("|")]
            head = cells(ln)
            i += 2
            body = []
            while i < len(lines) and "|" in lines[i]:
                body.append(cells(lines[i]))
                i += 1
            h = "<table><thead><tr>" + "".join(
                "<th>%s</th>" % md_inline(c) for c in head) + "</tr></thead><tbody>"
            for r in body:
                h += "<tr>" + "".join("<td>%s</td>" % md_inline(c) for c in r) + "</tr>"
            out.append(h + "</tbody></table>")
            continue

        if re.match(r"^\s*[-*+]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(md_inline(re.sub(r"^\s*[-*+]\s+", "", lines[i])))
                i += 1
            out.append("<ul>" + "".join("<li>%s</li>" % x for x in items) + "</ul>")
            continue

        if re.match(r"^\s*\d+[.)]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+[.)]\s+", lines[i]):
                items.append(md_inline(re.sub(r"^\s*\d+[.)]\s+", "", lines[i])))
                i += 1
            out.append("<ol>" + "".join("<li>%s</li>" % x for x in items) + "</ol>")
            continue

        if not ln.strip():
            i += 1
            continue

        para = []
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|```|\s*[-*+]\s|\s*\d+[.)]\s)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + md_inline(" ".join(para)) + "</p>")

    return "\n".join(out)


DOC_CSS = """<style>
*{box-sizing:border-box}
body{margin:0;background:#0E131C;color:#E6EAF2;
  font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:32px 22px 80px}
a{color:#8fa2ff}
h1{font-size:28px;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:20px;margin:34px 0 10px;border-top:1px solid #263144;padding-top:22px}
h3{font-size:16px;margin:22px 0 6px}
h4{font-size:14px;margin:18px 0 4px;opacity:.8}
p,li{color:#c9d2e0}
code{background:#1E2738;border:1px solid #263144;border-radius:5px;
  padding:1px 5px;font-size:13.5px;font-family:ui-monospace,Menlo,monospace}
pre{background:#161D2B;border:1px solid #263144;border-radius:10px;
  padding:14px;overflow:auto}
pre code{background:none;border:0;padding:0;font-size:13px;line-height:1.5}
hr{border:0;border-top:1px solid #263144;margin:26px 0}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14px}
th,td{border:1px solid #263144;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#161D2B}
.back{display:inline-block;margin-bottom:20px;color:#8fa2ff;text-decoration:none}
.doclist{list-style:none;padding:0}
.doclist li{margin:8px 0}
.doclist a{font-size:17px}
</style>"""


def doc_page(title, body):
    return ("<!doctype html><html><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>" + md_escape(title) + " - xxOps</title>" + DOC_CSS +
            "</head><body><div class=\"wrap\">" + body + "</div></body></html>")
