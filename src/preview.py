#!/usr/bin/env python3
"""Render templates/page.html against the design-sample fixtures for local preview.

Usage:
    python3 src/preview.py --out /tmp/cheatsheet-preview.html [--empty]

This is a design-preview helper only — it has nothing to do with the real
build (src/build.py owns that). It string-replaces the same seven {{TOKEN}}s
documented in templates/CONTRACT.md with the sample fragments in
templates/fixtures/, so the page shell can be reviewed and screenshotted
without running the real engine. It also copies docs/manifest.webmanifest and
docs/icons/ next to the output file so relative paths resolve the same way
they do on the live GitHub Pages site.

Fails loudly (non-zero exit) if any {{TOKEN}} is left unreplaced in the
rendered output, so a drifted template/fixture pair can't pass silently.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "templates" / "page.html"
FIXTURES = REPO_ROOT / "templates" / "fixtures"
MANIFEST = REPO_ROOT / "docs" / "manifest.webmanifest"
ICONS_DIR = REPO_ROOT / "docs" / "icons"

# The seven tokens templates/CONTRACT.md says build.py always replaces.
TOKENS = (
    "TODAY",
    "WEEKEND",
    "LEGS",
    "RECORD",
    "UPDATED_AT",
    "NEXT_UPDATE",
    "SAMPLE_BANNER",
)

TOKEN_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def read(path: Path) -> str:
    if not path.exists():
        sys.exit(f"preview.py: missing file: {path}")
    return path.read_text(encoding="utf-8")


def render(empty: bool) -> str:
    page = read(TEMPLATE)

    today_file = "today_empty.html" if empty else "today.html"
    values = {
        "TODAY": read(FIXTURES / today_file),
        "WEEKEND": read(FIXTURES / "weekend.html"),
        "LEGS": read(FIXTURES / "legs.html"),
        "RECORD": read(FIXTURES / "record.html"),
        "UPDATED_AT": "Updated Sun 9:04 AM ET",
        "NEXT_UPDATE": "Next card: Mon ~9 AM",
        "SAMPLE_BANNER": "",
    }

    for token in TOKENS:
        page = page.replace("{{" + token + "}}", values[token])

    leftover = sorted(set(TOKEN_RE.findall(page)))
    if leftover:
        sys.exit(
            "preview.py: unreplaced token(s) left in rendered page: "
            + ", ".join(leftover)
            + " — templates/page.html and src/preview.py have drifted apart."
        )

    return page


def copy_static_assets(out_dir: Path) -> None:
    if MANIFEST.exists():
        shutil.copy2(MANIFEST, out_dir / "manifest.webmanifest")
    else:
        print(f"preview.py: warning — {MANIFEST} not found, skipping", file=sys.stderr)

    if ICONS_DIR.exists():
        dest_icons = out_dir / "icons"
        dest_icons.mkdir(exist_ok=True)
        for icon in sorted(ICONS_DIR.iterdir()):
            if icon.is_file():
                shutil.copy2(icon, dest_icons / icon.name)
    else:
        print(f"preview.py: warning — {ICONS_DIR} not found, skipping", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="path to write the rendered preview HTML")
    parser.add_argument("--empty", action="store_true", help="use the empty-state Today fixture")
    args = parser.parse_args()

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    page = render(args.empty)
    out_path.write_text(page, encoding="utf-8")
    copy_static_assets(out_path.parent)

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
