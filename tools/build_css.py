"""U-03: rebuild static/pulse.css with the Tailwind standalone CLI (replaces the Play-CDN).

Usage: python tools/build_css.py [path-to-tailwindcss-binary]
The binary (https://github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17) is not part of the
repository; pass its path or set TAILWIND_BIN. Run this after adding new Tailwind classes and commit
the resulting static/pulse.css – the server itself never needs the binary.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    binary = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TAILWIND_BIN", "tailwindcss")
    cmd = [binary, "-c", str(ROOT / "tailwind.config.js"), "-i", str(ROOT / "static" / "tailwind.input.css"),
           "-o", str(ROOT / "static" / "pulse.css"), "--minify"]
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode == 0:
        print(f"static/pulse.css: {(ROOT / 'static' / 'pulse.css').stat().st_size / 1024:.0f} KB")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
