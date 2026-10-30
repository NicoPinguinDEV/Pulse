"""CI helper: decide whether the browser E2E suite has to run for a set of changed files.

    git diff --name-only BASE HEAD | python tools/ci_e2e_needed.py

Prints `true` or `false` (also appended as `e2e=…` to $GITHUB_OUTPUT when set). Deny-list on purpose: only
files that cannot change what the dashboard serves are skipped, every other path (including unknown new ones)
triggers the suite. An empty diff counts as "unknown" and runs it as well.
"""
from __future__ import annotations

import os
import sys
from fnmatch import fnmatch

# Paths that never reach the running dashboard. Keep this list short – when in doubt, run E2E.
IRRELEVANT = (
    "*.md",
    "docs/*",
    ".claude/*",
    "pyrightconfig.json",
    "LICENSE*",
    ".gitignore",
    ".gitattributes",
    ".github/ISSUE_TEMPLATE/*",
    ".github/workflows/security.yml",
    ".github/workflows/deploy-vps.yml",
)
# Unit tests do not change the app; the E2E test, its fakes and the preview server do. Markdown that Pulse
# reads at runtime (cogs/support_assistant.py) is not documentation.
ALWAYS_RELEVANT = ("tests/test_e2e_*.py", "tests/fakes.py", "support_knowledge.md", "team_ki_vorlagen.md")


def needs_e2e(paths: list[str]) -> bool:
    paths = [p.strip().replace("\\", "/") for p in paths if p.strip()]
    if not paths:
        return True
    for path in paths:
        if any(fnmatch(path, pattern) for pattern in ALWAYS_RELEVANT):
            return True
        if fnmatch(path, "tests/*.py") or fnmatch(path, "tests/*.json"):
            continue
        if not any(fnmatch(path, pattern) for pattern in IRRELEVANT):
            return True
    return False


def main() -> int:
    result = "true" if needs_e2e(sys.stdin.read().splitlines()) else "false"
    print(result)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"e2e={result}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
