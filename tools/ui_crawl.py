"""Audit #64-#66: UI sweep over all parameter-free GET routes of the preview server (fake admin, no Discord).

    python tools/ui_crawl.py [output.json]

Visits every page at 375 px (mobile) and 1280 px (desktop) in the dark and the light theme and reports per page: HTTP
status, JavaScript errors, horizontal page overflow, elements sticking out of the viewport, tap targets under 24 px
and text with a contrast under 3:1 (elements on gradients are skipped). A read-only helper for manual reviews; the
permanent regression checks live in tests/test_e2e_dashboard.py (AccessibilityE2ETests). Needs Playwright
(python -m playwright install chromium)."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "pulse_ui_crawl.json"
routes = []
for line in json.load(open(ROOT / "docs" / "routen_snapshot.json", encoding="utf-8")):
    method, rest = line.split(" ", 1)
    path = rest.split(" -> ")[0]
    if method == "GET" and "{" not in path and not path.endswith((".csv", ".css", ".js", ".ico", ".svg")) and not any(
            x in path for x in ("/api/", "/logout", "/login", "/callback", "/download", "/export", "/stream", "/static", "/__preview", "/health", "/status", "/robots", "/sitemap", "/transcript", "/media", "/events.ics", ".json")):
        routes.append(path)
routes = sorted(set(routes))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
tmp = tempfile.mkdtemp()
env = dict(os.environ, PYTHONUTF8="1", TMP=tmp, TEMP=tmp, PULSE_GITHUB_REPO="")
env.pop("DISCORD_TOKEN", None)
log = open(os.path.join(tmp, "l.txt"), "w")
srv = subprocess.Popen([sys.executable, str(ROOT / "tools" / "preview_server.py"), "--port", str(port), "--role", "admin", "--e2e-hooks"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
for _ in range(120):
    try:
        socket.create_connection(("127.0.0.1", port), 0.5).close(); break
    except OSError:
        time.sleep(0.5)

JS = """() => {
  const vw = window.innerWidth, issues = {};
  issues.overflow = document.documentElement.scrollWidth - vw;
  const wide = [];
  for (const el of document.querySelectorAll('main *')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 2 && !el.closest('[style*="overflow"], .overflow-x-auto, table, pre, svg') ) {
      let p = el.parentElement, scroll = false;
      while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { scroll = true; break; } p = p.parentElement; }
      if (!scroll) wide.push(el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '') + ':' + Math.round(r.right));
    }
    if (wide.length >= 5) break;
  }
  issues.wide = wide;
  const small = [];
  for (const el of document.querySelectorAll('main a, main button, main input, main select')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && (r.height < 24 || r.width < 24) && getComputedStyle(el).visibility !== 'hidden' && el.type !== 'hidden' && el.type !== 'checkbox' && el.type !== 'radio') small.push((el.textContent || el.name || el.tagName).trim().slice(0, 20) + ':' + Math.round(r.width) + 'x' + Math.round(r.height));
    if (small.length >= 5) break;
  }
  issues.small = small;
  // contrast: sample visible text nodes' colour vs. nearest opaque background
  function lum(c) { const m = c.match(/\\d+(\\.\\d+)?/g); if (!m) return null; const [r, g, b] = m.slice(0, 3).map(Number).map(v => { v /= 255; return v <= .03928 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4); }); return .2126 * r + .7152 * g + .0722 * b; }
  function bg(el) { while (el) { if (getComputedStyle(el).backgroundImage !== 'none') return null; const c = getComputedStyle(el).backgroundColor; const m = c.match(/[\\d.]+/g); if (m && (m.length === 3 || Number(m[3]) > .9)) return c; el = el.parentElement; } return 'rgb(11,14,20)'; }
  const low = [];
  for (const el of document.querySelectorAll('main p, main span, main td, main th, main label, main a, main div, main li, main h1, main h2, main h3, main button')) {
    if (!el.childNodes.length || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 2)) continue;
    const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
    const st = getComputedStyle(el); if (st.visibility === 'hidden' || st.opacity === '0') continue;
    const bgc = bg(el); if (bgc == null) continue; const l1 = lum(st.color), l2 = lum(bgc); if (l1 == null || l2 == null) continue;
    const ratio = (Math.max(l1, l2) + .05) / (Math.min(l1, l2) + .05);
    const size = parseFloat(st.fontSize);
    if (ratio < 3.0 && size < 40) low.push(el.textContent.trim().slice(0, 24) + ' ' + ratio.toFixed(2) + ' ' + st.color + ' on ' + bgc);
    if (low.length >= 4) break;
  }
  issues.contrast = low;
  return issues;
}"""

results = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    for vname, vp, mobile in (("mobile", {"width": 375, "height": 812}, True), ("desktop", {"width": 1280, "height": 900}, False)):
        for scheme in ("dark", "light"):
            ctx = b.new_context(viewport=vp, is_mobile=mobile, has_touch=mobile, color_scheme=scheme)
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else r.abort())
            ctx.add_init_script("localStorage.theme='%s'" % scheme)
            page = ctx.new_page(); page.set_default_timeout(15000)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)[:120]))
            for path in routes:
                errors.clear()
                try:
                    resp = page.goto(f"http://127.0.0.1:{port}{path}", wait_until="load")
                    page.wait_for_timeout(150)
                    status = resp.status if resp else 0
                    if status >= 400:
                        results.append({"path": path, "view": vname, "scheme": scheme, "status": status}); continue
                    issues = page.evaluate(JS)
                    row = {"path": path, "view": vname, "scheme": scheme, "status": status, "js": list(errors), **issues}
                    results.append(row)
                except Exception as exc:
                    results.append({"path": path, "view": vname, "scheme": scheme, "error": str(exc)[:100]})
            ctx.close()
    b.close()
srv.terminate()
json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print(len(routes), "routes;", len(results), "checks")
