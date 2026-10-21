"""U-05: shared list tools – search, sorting, pagination and a CSV export that always goes through csv_safe.

Pages keep their own markup; this module only cuts the rows and builds the small pager/search pieces,
so long lists (audit, warnings …) no longer render hundreds or thousands of entries at once.
"""
from __future__ import annotations

import csv
import io
from html import escape
from urllib.parse import urlencode

PER_PAGE_CHOICES = (25, 50, 100)
DEFAULT_PER_PAGE = 50


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def params(query, prefix: str = "") -> dict:
    """Reads q/page/per/sort (optionally prefixed, e.g. 'l' for a second list on the same page)."""
    per = _int(query.get(f"{prefix}per"), DEFAULT_PER_PAGE)
    return {"q": str(query.get("q") or "").strip()[:200], "page": max(1, _int(query.get(f"{prefix}page"), 1)),
            "per": per if per in PER_PAGE_CHOICES else DEFAULT_PER_PAGE, "sort": str(query.get(f"{prefix}sort") or "")}


def search(rows: list, q: str, text) -> list:
    q = (q or "").strip().lower()
    return [r for r in rows if q in str(text(r)).lower()] if q else list(rows)


def paginate(rows: list, page: int, per: int) -> tuple[list, dict]:
    total = len(rows)
    pages = max(1, -(-total // per))
    page = min(max(1, page), pages)
    start = (page - 1) * per
    return rows[start:start + per], {"page": page, "pages": pages, "per": per, "total": total,
                                     "first": start + 1 if total else 0, "last": min(start + per, total)}


def pager(path: str, meta: dict, query: dict, prefix: str = "", anchor: str = "") -> str:
    """'1–50 von 312' plus previous/next links that keep the other query parameters."""
    if meta["total"] <= meta["per"] and meta["page"] == 1:
        return f'<div class="pulse-row-meta mt-2">{meta["total"]} Einträge</div>' if meta["total"] else ""

    def link(page: int, label: str, enabled: bool, rel: str) -> str:
        if not enabled:
            return f'<span class="pulse-btn ghost" aria-disabled="true" style="opacity:.45">{label}</span>'
        args = {k: v for k, v in query.items() if v not in ("", None)}
        args[f"{prefix}page"] = page
        return f'<a class="pulse-btn ghost" rel="{rel}" href="{escape(path)}?{escape(urlencode(args))}{anchor}">{label}</a>'

    return (f'<nav class="flex flex-wrap items-center gap-2 mt-3" aria-label="Seiten">'
            f'{link(meta["page"] - 1, "← Zurück", meta["page"] > 1, "prev")}'
            f'<span class="pulse-row-meta">{meta["first"]}–{meta["last"]} von {meta["total"]} · Seite {meta["page"]}/{meta["pages"]}</span>'
            f'{link(meta["page"] + 1, "Weiter →", meta["page"] < meta["pages"], "next")}</nav>')


def csv_safe(value) -> str:
    """Same rule as webserver.csv_safe: values starting with = + - @ are not run as spreadsheet formulas."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def csv_response(filename: str, header: list, rows):
    """Excel-friendly CSV (BOM, semicolon), every cell through csv_safe."""
    from fastapi.responses import Response
    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_safe(v) for v in row])
    return Response(out.getvalue().encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
