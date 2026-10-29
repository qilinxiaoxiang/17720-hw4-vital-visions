"""Inline schedule.js and background.png into transmitter.html (in place).

transmitter.html stays one self-contained file (no network, no sibling files
needed at run time). Run this after editing schedule.js or regenerating the
picture; tests/test_transmitter_html.py fails if the inlined copies are stale.
"""
import base64
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HTML = HERE / "transmitter.html"

JS_RE = re.compile(r"(/\* BEGIN schedule\.js \*/\n)(.*?)(/\* END schedule\.js \*/)", re.S)
BG_RE = re.compile(r"(/\* BEGIN background\.png \*/)(.*?)(/\* END background\.png \*/)", re.S)


def expected_parts():
    js = (HERE / "schedule.js").read_text()
    uri = "data:image/png;base64," + base64.b64encode((HERE / "background.png").read_bytes()).decode()
    return js, "'" + uri + "'"


def build(html: str) -> str:
    js, bg = expected_parts()
    if not JS_RE.search(html) or not BG_RE.search(html):
        raise SystemExit("markers not found in transmitter.html")
    html = JS_RE.sub(lambda m: m.group(1) + js + m.group(3), html)
    html = BG_RE.sub(lambda m: m.group(1) + bg + m.group(3), html)
    return html


def is_current(html: str) -> bool:
    return build(html) == html


if __name__ == "__main__":
    src = HTML.read_text()
    if "--check" in sys.argv:
        ok = is_current(src)
        print("transmitter.html is", "current" if ok else "STALE (run build_transmitter.py)")
        sys.exit(0 if ok else 1)
    HTML.write_text(build(src))
    print(f"wrote {HTML} ({HTML.stat().st_size // 1024} KB)")
