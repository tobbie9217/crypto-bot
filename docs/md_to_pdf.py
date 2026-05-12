"""Convert deep_research.md to deep_research.pdf using xhtml2pdf.

Run from the project root:
    python docs/md_to_pdf.py
"""
import sys
from pathlib import Path

import markdown
from xhtml2pdf import pisa

HERE = Path(__file__).parent
SRC = HERE / "deep_research.md"
DST = HERE / "deep_research.pdf"

CSS = """
@page {
    size: letter;
    margin: 2cm 2.2cm 2cm 2.2cm;
}
body {
    font-family: Helvetica, Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #222;
}
h1 {
    font-size: 20pt;
    color: #111;
    border-bottom: 2px solid #333;
    padding-bottom: 0.2em;
    margin-top: 1.8em;
    margin-bottom: 0.6em;
}
h1:first-of-type { margin-top: 0; }
h2 {
    font-size: 15pt;
    color: #222;
    border-bottom: 1px solid #ccc;
    padding-bottom: 0.15em;
    margin-top: 1.6em;
    margin-bottom: 0.5em;
}
h3 {
    font-size: 12.5pt;
    color: #333;
    margin-top: 1.2em;
    margin-bottom: 0.3em;
}
p { margin: 0.5em 0; }
em { color: #555; font-style: italic; }
strong { color: #111; }
hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 1.5em 0;
}
ul, ol { margin: 0.4em 0 0.8em 1.4em; padding: 0; }
li { margin-bottom: 0.18em; }
code {
    font-family: "Courier New", Courier, monospace;
    background-color: #f4f4f4;
    padding: 1px 4px;
    border-radius: 2px;
    font-size: 9.5pt;
    color: #c7254e;
}
pre {
    background-color: #f4f4f4;
    border: 1px solid #ddd;
    border-radius: 3px;
    padding: 10px 14px;
    font-family: "Courier New", Courier, monospace;
    font-size: 9pt;
    line-height: 1.35;
    color: #333;
    white-space: pre-wrap;
    word-wrap: break-word;
}
pre code {
    background: transparent;
    padding: 0;
    color: inherit;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.9em 0;
    font-size: 9.5pt;
}
th {
    background-color: #2c3e50;
    color: white;
    padding: 6px 8px;
    text-align: left;
    border: 1px solid #2c3e50;
    font-weight: bold;
}
td {
    padding: 5px 8px;
    border: 1px solid #ddd;
    vertical-align: top;
}
tr:nth-child(even) td { background-color: #f9f9f9; }
blockquote {
    margin: 0.8em 0;
    padding-left: 12px;
    border-left: 3px solid #888;
    color: #555;
    font-style: italic;
}
"""


def main() -> int:
    if not SRC.exists():
        print(f"missing source: {SRC}", file=sys.stderr)
        return 1

    md_text = SRC.read_text(encoding="utf-8")
    html_body = markdown.markdown(
        md_text,
        extensions=["extra", "tables", "fenced_code", "sane_lists"],
    )
    html_doc = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>{html_body}</body></html>"""

    with DST.open("wb") as f:
        result = pisa.CreatePDF(html_doc, dest=f, encoding="utf-8")

    if result.err:
        print(f"PDF generation reported {result.err} error(s)", file=sys.stderr)
        return 2

    size_kb = DST.stat().st_size / 1024
    print(f"wrote {DST.name}  ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
