#!/usr/bin/env python3
"""Bundle the web app into a single compressed file for the board.

    python3 tools/build.py

The Pico serves HTTP one request at a time and renders nothing while it is
doing so, so six separate files for the page meant six round trips with the
lights frozen in between.  This inlines the stylesheet and every script into
one document, gzips it, and writes an ETag beside it so a browser that has
been here before gets a 304 instead of the whole page.

Sources live in src/ and are never served directly.
"""

import gzip
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
OUT_DIR = os.path.join(ROOT, "www")
PAGE = os.path.join(OUT_DIR, "index.html.gz")
ETAG = PAGE + ".etag"

LINK = re.compile(r'[ \t]*<link rel="stylesheet" href="/([^"]+)">[ \t]*\n?')
SCRIPT = re.compile(r'[ \t]*<script src="/([^"]+)"></script>[ \t]*\n?')


def read(name):
    path = os.path.join(SRC, name)
    if not os.path.isfile(path):
        raise SystemExit("missing source file: %s" % path)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def bundle():
    html = read("index.html")
    used = ["index.html"]

    def inline_style(match):
        name = match.group(1)
        used.append(name)
        return "<style>\n%s\n</style>\n" % read(name).strip()

    def inline_script(match):
        name = match.group(1)
        used.append(name)
        # </script> inside a string would end the block early; nothing here
        # contains one, but guard anyway so a future edit cannot break the
        # page silently.
        body = read(name).replace("</script>", "<\\/script>")
        return "<script>\n%s\n</script>\n" % body.strip()

    html = LINK.sub(inline_style, html)
    html = SCRIPT.sub(inline_script, html)

    leftover = re.findall(r'(?:src|href)="/(?!api/)([^"]+)"', html)
    if leftover:
        raise SystemExit(
            "these would still be separate requests, so they must be inlined: %s"
            % ", ".join(sorted(set(leftover)))
        )
    return html, used


def main():
    html, used = bundle()
    raw = html.encode("utf-8")

    os.makedirs(OUT_DIR, exist_ok=True)
    # mtime=0 keeps the output byte-identical for identical input, so a
    # rebuild that changes nothing does not show up as a diff.
    with gzip.GzipFile(PAGE, "wb", compresslevel=9, mtime=0) as handle:
        handle.write(raw)

    digest = hashlib.sha256(raw).hexdigest()[:16]
    with open(ETAG, "w", encoding="utf-8") as handle:
        handle.write(digest + "\n")

    packed = os.path.getsize(PAGE)
    print("bundled %d files -> %s" % (len(used), os.path.relpath(PAGE, ROOT)))
    for name in used:
        print("    %s" % name)
    print(
        "%d requests -> 1   %.0f kB -> %.0f kB gzipped (%.0f%%)   etag %s"
        % (len(used), len(raw) / 1024, packed / 1024, 100 * packed / len(raw), digest)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
