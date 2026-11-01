"""Erzeugt docs/JOBS_UND_RECHTE.md: Hintergrundschleifen und Rechte-Schlüssel mit Herkunft.

Aufruf (aus dem Repo-Wurzelverzeichnis):
    python tools/jobs_perms.py

Statische Analyse; ändert nichts am Code.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"tests", "deps", "venv", ".venv", ".git", "__pycache__", "tools"}


def py_files():
    for path in sorted(ROOT.rglob("*.py")):
        if not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            yield path


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def error_handling(node: ast.AST) -> str:
    handlers = [h for h in ast.walk(node) if isinstance(h, ast.ExceptHandler)]
    if not handlers:
        return "keine (Fehler beenden die Schleife bzw. werden von discord.ext.tasks geloggt)"
    silent = [h for h in handlers if len(h.body) == 1 and isinstance(h.body[0], ast.Pass)]
    broad = [h for h in handlers if h.type is None or (isinstance(h.type, ast.Name) and h.type.id in ("Exception", "BaseException"))]
    text = f"{len(handlers)} except"
    if broad:
        text += f", davon {len(broad)} breit"
    if silent:
        text += f", **{len(silent)} still (`pass`)**"
    return text


def main() -> int:
    loops, endless, spawns = [], [], []
    perm_reads = defaultdict(lambda: defaultdict(int))
    perm_grants = defaultdict(set)
    for path in py_files():
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        spawn_aliases = {
            alias.asname or alias.name
            for item in ast.walk(tree)
            if isinstance(item, ast.ImportFrom) and item.module == "pulse_tasks"
            for alias in item.names
            if alias.name == "spawn"
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    target = deco.func if isinstance(deco, ast.Call) else deco
                    if isinstance(target, ast.Attribute) and target.attr == "loop" and getattr(target.value, "id", "") == "tasks":
                        args = ", ".join(ast.get_source_segment(text, kw) or "" for kw in getattr(deco, "keywords", []))
                        handling = error_handling(node)
                        if any("tracked" in (getattr(t, "id", ""), getattr(t, "attr", "")) for t in (d.func if isinstance(d, ast.Call) else d for d in node.decorator_list)):
                            # Phase 14.6: @tracked records the error and keeps the loop running (backoff for fast loops)
                            handling = handling.replace("keine (Fehler beenden die Schleife bzw. werden von discord.ext.tasks geloggt)", "keine eigene") + "; `@tracked`: Fehler erfasst, Schleife läuft weiter"
                        loops.append((rel(path), node.lineno, node.name, args or "(Standard)", handling))
                if isinstance(node, ast.AsyncFunctionDef):
                    for inner in ast.walk(node):
                        if isinstance(inner, ast.While) and (
                            (isinstance(inner.test, ast.Constant) and inner.test.value is True)
                            or "is_closed" in (ast.get_source_segment(text, inner.test) or "")):
                            sleeps = re.findall(r"asyncio\.sleep\(([^)]*)\)", ast.get_source_segment(text, inner) or "")
                            if not sleeps:
                                continue  # e.g. chunked upload reading – not a background job
                            endless.append((rel(path), inner.lineno, node.name, ", ".join(sleeps[:3]) or "?", error_handling(inner)))
                            break
            if isinstance(node, ast.Call):
                func = node.func
                direct_task = (
                    isinstance(func, ast.Attribute)
                    and func.attr in ("create_task", "ensure_future")
                    and getattr(func.value, "id", "") != "db"
                )
                managed_task = isinstance(func, ast.Name) and func.id in spawn_aliases
                if direct_task or managed_task:
                    call = " ".join((ast.get_source_segment(text, node) or "").split())
                    spawns.append((rel(path), node.lineno, call[:90]))
        for key in re.findall(r"perms\.get\(\s*['\"]([a-z_]+)['\"]", text):
            perm_reads[key][rel(path)] += 1
        for key in re.findall(r"has_perm\([^,]+,\s*['\"]([a-z_]+)['\"]", text):
            perm_reads[key][rel(path)] += 1
        for key in re.findall(r"perms\[\s*['\"]([a-z_]+)['\"]\s*\]\s*=\s*True", text):
            perm_grants[key].add(rel(path))

    ws = (ROOT / "webserver.py").read_text(encoding="utf-8")
    pro = (ROOT / "pulse_pro.py").read_text(encoding="utf-8")
    perm_keys = re.search(r"^PERM_KEYS\s*=\s*[\[(](.*?)[\])]", ws, re.S | re.M)
    extra = re.search(r"^EXTRA_PERMS\s*=\s*\((.*?)\)", pro, re.S | re.M)
    base_keys = re.findall(r"['\"]([a-z_]+)['\"]", perm_keys.group(1)) if perm_keys else []
    extra_keys = re.findall(r"['\"]([a-z_]+)['\"]", extra.group(1)) if extra else []

    lines = ["# Hintergrund-Jobs und Rechte-Schlüssel", "",
             "Automatisch erzeugt von `python tools/jobs_perms.py` (statische Analyse).", "",
             f"## `@tasks.loop` ({len(loops)})", "", "| Datei:Zeile | Funktion | Intervall | Fehlerbehandlung |", "|---|---|---|---|"]
    lines += [f"| {f}:{l} | `{n}` | `{a}` | {e} |" for f, l, n, a, e in loops]
    lines += ["", f"## Eigene Endlosschleifen ({len(endless)})", "", "| Datei:Zeile | Funktion | Pause (`asyncio.sleep`) | Fehlerbehandlung |", "|---|---|---|---|"]
    lines += [f"| {f}:{l} | `{n}` | `{s}` | {e} |" for f, l, n, s, e in endless]
    lines += ["", f"## `create_task` / `ensure_future` / `pulse_tasks.spawn` ({len(spawns)})", "",
              "`pulse_tasks.spawn` hält Tasks bis zum Abschluss, protokolliert unbehandelte Fehler und begrenzt normale Hintergrundarbeit; kritische Kernaufgaben können die weiche Obergrenze übergehen.", "",
              "| Datei:Zeile | Aufruf |", "|---|---|"]
    lines += [f"| {f}:{l} | `{c.replace('|', '¦')}` |" for f, l, c in spawns]
    lines += ["", "## Rechte-Schlüssel", "",
              f"- **Basis (`webserver.PERM_KEYS`, je Discord-Teamrolle in `config.permissions`):** {', '.join(f'`{k}`' for k in base_keys)}",
              f"- **Erweitert (`pulse_pro.EXTRA_PERMS`, zusätzlich über Dashboard-Profile `dashboard_roles`):** {', '.join(f'`{k}`' for k in extra_keys)}",
              "- **Weitere Ebenen:** Nutzer-Overrides `next_permission_overrides` (`pulse_next.has_perm`), Slash-Befehlsrollen `command_roles` (`cogs/pulse_suite.py`).", "",
              "| Schlüssel | Herkunft | gelesen in (Anzahl) | implizit auf True gesetzt in |", "|---|---|---|---|"]
    all_keys = sorted(set(perm_reads) | set(base_keys) | set(extra_keys) | set(perm_grants))
    for key in all_keys:
        origin = "PERM_KEYS" if key in base_keys else "EXTRA_PERMS" if key in extra_keys else "nur im Code"
        reads = ", ".join(f"{f} ({n})" for f, n in sorted(perm_reads[key].items())) or "—"
        grants = ", ".join(sorted(perm_grants[key])) or "—"
        lines.append(f"| `{key}` | {origin} | {reads} | {grants} |")
    (ROOT / "docs" / "JOBS_UND_RECHTE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"tasks.loop: {len(loops)} · Endlosschleifen: {len(endless)} · Hintergrund-Tasks: {len(spawns)} · Rechte-Schlüssel: {len(all_keys)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
