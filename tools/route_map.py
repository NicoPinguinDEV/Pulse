"""Erzeugt docs/ROUTEN.md und docs/routen_snapshot.json aus dem *laufenden* FastAPI-Objekt.

Aufruf (aus dem Repo-Wurzelverzeichnis):
    python tools/route_map.py

* Importiert `webserver` mit einer Temp-Datenbank (PULSE_DB_PATH) – keine Discord-Verbindung.
* Effektive Routen: Methode, Pfad, Modul/Funktion, Datei:Zeile, erkannte Rechteprüfung.
* Verdrängte Handler: alle im Quelltext registrierten Routen (Decorator, `app.get(...)(fn)`,
  `remove_and_add(...)`), deren Funktion zur Laufzeit nicht (mehr) aktiv ist.
* Doppelte (Methode, Pfad)-Kombinationen im laufenden Objekt.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"tests", "deps", "venv", ".venv", ".git", "__pycache__", "tools", "node_modules"}
HTTP_METHODS = {"get", "post", "put", "delete", "patch"}


def _auth_kind(source: str) -> str:
    """Best-effort Erkennung der Rechteprüfung im Handler-Quelltext."""
    found = []
    for match in re.finditer(r"\b(auth|cctx|ctx_for|admin_ctx|ctx_auth|ctx|require_perm|require_permission)\(([^()]*(?:\([^()]*\)[^()]*)*)\)", source):
        name, args = match.group(1), match.group(2)
        if name == "ctx" and "request" not in args:
            continue
        if "admin=True" in args or name == "admin_ctx":
            found.append("Admin")
        else:
            perm = re.search(r"perm\s*=\s*['\"]([a-z_]+)['\"]", args)
            if perm:
                found.append(perm.group(1))
            elif "perm=None" in args or name in ("ctx_for",):
                found.append("angemeldet" if name != "ctx_for" else "angemeldet + eigene Prüfung")
            else:
                found.append("can_view_dashboard" if name in ("auth", "cctx", "ctx_auth") else name)
    extra = re.findall(r"perms\.get\(['\"]([a-z_]+)['\"]\)", source)
    if found:
        result = ", ".join(dict.fromkeys(found))
        if extra:
            result += " (+ " + ", ".join(sorted(set(extra))[:4]) + ")"
        return result
    if re.search(r"validate_api_key\(|require_api_key\(|X-Pulse-API-Key", source):
        return "API-Schlüssel (X-Pulse-API-Key)"
    if re.search(r"get_current_user\(|apply_session|_apply_user\(", source):
        return "Session (eigene Prüfung)"
    return "— (öffentlich/keine erkennbare Prüfung)"


def effective_routes(app) -> list[dict]:
    rows = []
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        methods = sorted(m for m in (getattr(route, "methods", None) or []) if m not in ("HEAD",))
        if endpoint is None:
            continue
        try:
            file = Path(inspect.getsourcefile(endpoint)).resolve()
            line = inspect.getsourcelines(endpoint)[1]
            source = inspect.getsource(endpoint)
        except (OSError, TypeError):
            file, line, source = None, 0, ""
        rel = str(file.relative_to(ROOT)).replace("\\", "/") if file and ROOT in file.parents else str(file or "?")
        for method in methods or ["(mount)"]:
            rows.append({
                "method": method, "path": route.path, "module": getattr(endpoint, "__module__", "?"),
                "function": getattr(endpoint, "__qualname__", "?"), "file": rel, "line": line,
                "auth": _auth_kind(source) if source else "?",
            })
    return rows


def static_registrations() -> list[dict]:
    """Alle Routen, die im Quelltext registriert werden – unabhängig davon, ob sie aktiv bleiben."""
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                            and deco.func.attr in HTTP_METHODS and deco.args
                            and isinstance(deco.args[0], ast.Constant) and isinstance(deco.args[0].value, str)):
                        found.append({"method": deco.func.attr.upper(), "path": deco.args[0].value,
                                      "function": node.name, "file": rel, "line": node.lineno, "via": "Decorator"})
            if isinstance(node, ast.Call):
                func = node.func
                # app.get('/x', ...)(handler)
                if (isinstance(func, ast.Call) and isinstance(func.func, ast.Attribute)
                        and func.func.attr in HTTP_METHODS and func.args
                        and isinstance(func.args[0], ast.Constant) and isinstance(func.args[0].value, str)
                        and node.args and isinstance(node.args[0], ast.Name)):
                    found.append({"method": func.func.attr.upper(), "path": func.args[0].value,
                                  "function": node.args[0].id, "file": rel, "line": node.lineno, "via": "app.<methode>(…)(fn)"})
                # remove_and_add(app, '/x', {'GET'}, handler)
                if (isinstance(func, ast.Name) and func.id == "remove_and_add" and len(node.args) >= 4
                        and isinstance(node.args[1], ast.Constant) and isinstance(node.args[2], (ast.Set, ast.List, ast.Tuple))
                        and isinstance(node.args[3], ast.Name)):
                    for elt in node.args[2].elts:
                        if isinstance(elt, ast.Constant):
                            found.append({"method": str(elt.value).upper(), "path": node.args[1].value,
                                          "function": node.args[3].id, "file": rel, "line": node.lineno, "via": "remove_and_add"})
    return found


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="pulse-routes-")
    os.environ.setdefault("PULSE_DB_PATH", str(Path(tmp) / "pulse.db"))
    sys.path.insert(0, str(ROOT))
    import webserver  # noqa: E402  (registriert alle Module am Dateiende)
    # Die Operations Suite registriert ihre Seiten erst beim Laden des Cogs (PulseSuite.__init__).
    import cogs.pulse_suite as suite  # noqa: E402
    suite.DB_PATH = str(Path(tmp) / "pulse_suite.db")  # never touch the real suite database
    suite.register_routes()

    routes = effective_routes(webserver.app)
    static = static_registrations()
    counts = Counter((r["method"], r["path"]) for r in routes)
    duplicates = sorted(k for k, n in counts.items() if n > 1)
    active_funcs = {(r["path"], r["method"], r["function"].split(".")[-1], r["file"]) for r in routes}
    displaced = [s for s in static
                 if (s["path"], s["method"], s["function"], s["file"]) not in active_funcs
                 and s["path"] in {r["path"] for r in routes}]
    unregistered = [s for s in static if s["path"] not in {r["path"] for r in routes}]

    snapshot = sorted({f'{r["method"]} {r["path"]} -> {r["module"]}.{r["function"]}' for r in routes})
    (ROOT / "docs" / "routen_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    by_area = defaultdict(list)
    for r in sorted(routes, key=lambda r: (r["path"], r["method"])):
        area = "/" + r["path"].strip("/").split("/")[0] if r["path"] != "/" else "/"
        by_area[area].append(r)

    lines = [
        "# Routen-Landkarte (effektiv zur Laufzeit)",
        "",
        "Automatisch erzeugt von `python tools/route_map.py` aus dem laufenden FastAPI-Objekt (nicht von Hand bearbeiten).",
        "Spalte *Prüfung* ist eine Quelltext-Heuristik (`auth(...)`, `cctx`, `ctx_for`, `admin_ctx`, `perm=`) – Hinweis, kein Beweis.",
        "",
        f"- **Effektive Routen (Methode × Pfad):** {len(routes)}",
        f"- **Doppelt registriert:** {len(duplicates)}" + ("" if not duplicates else " → " + ", ".join(f"`{m} {p}`" for m, p in duplicates)),
        f"- **Im Quelltext registriert, zur Laufzeit verdrängt:** {len(displaced)}",
        f"- **Ohne erkennbare Prüfung:** {sum(1 for r in routes if r['auth'].startswith('—'))} (Login/OAuth/Health/Bewerbungsportal/öffentliche Seiten – einzeln prüfen)",
        "",
        "> Achtung `remove_and_add` (`pulse_pro.py`): ersetzt nur die angegebenen HTTP-Methoden; andere Methoden desselben Pfads bleiben erhalten.",
        "",
    ]
    if duplicates:
        lines += ["## Doppelte Registrierungen", "", "| Methode | Pfad | aktive Handler (Reihenfolge) |", "|---|---|---|"]
        for method, path in duplicates:
            handlers = [f'`{r["module"]}.{r["function"]}` ({r["file"]}:{r["line"]})' for r in routes if (r["method"], r["path"]) == (method, path)]
            lines.append(f"| {method} | `{path}` | " + " → ".join(handlers) + " (FastAPI nimmt den **ersten** Treffer) |")
        lines.append("")
    lines += ["## Verdrängte Handler (toter Code)", "", "| Methode | Pfad | verdrängte Funktion | Ort | aktiv stattdessen |", "|---|---|---|---|---|"]
    for s in sorted(displaced, key=lambda s: (s["path"], s["method"])):
        active = next((r for r in routes if (r["method"], r["path"]) == (s["method"], s["path"])), None)
        now = f'`{active["module"]}.{active["function"]}`' if active else "— (Methode entfernt!)"
        lines.append(f'| {s["method"]} | `{s["path"]}` | `{s["function"]}` | {s["file"]}:{s["line"]} | {now} |')
    if unregistered:
        lines += ["", "## Im Quelltext, aber zur Laufzeit gar nicht vorhanden", "",
                  "| Methode | Pfad | Funktion | Ort |", "|---|---|---|---|"]
        for s in sorted(unregistered, key=lambda s: (s["path"], s["method"])):
            lines.append(f'| {s["method"]} | `{s["path"]}` | `{s["function"]}` | {s["file"]}:{s["line"]} |')
    lines += ["", "## Effektive Routen nach Bereich", ""]
    for area in sorted(by_area):
        lines += [f"### `{area}` ({len(by_area[area])})", "", "| Methode | Pfad | Handler | Ort | Prüfung |", "|---|---|---|---|---|"]
        for r in by_area[area]:
            lines.append(f'| {r["method"]} | `{r["path"]}` | `{r["module"]}.{r["function"]}` | {r["file"]}:{r["line"]} | {r["auth"]} |')
        lines.append("")
    (ROOT / "docs" / "ROUTEN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Routen: {len(routes)} · doppelt: {len(duplicates)} · verdrängt: {len(displaced)} · fehlend: {len(unregistered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
