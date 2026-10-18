"""One design system for every Pulse page.

The Ultimate/TeamOS-2.0/Intelligence pages and the Operations Suite used to ship their own
dark-only shells with separate navigations. They now render inside the main Pulse shell
(same sidebar, command palette, light/dark theme). Their existing component classes
(.card, .row, .btn …) are mapped onto the shared design tokens under `.pulse-legacy`, so
page bodies keep working unchanged.
"""
from __future__ import annotations

# (key, href, label, visible_for_everyone)
ULTIMATE_NAV = (
    ("ultimate", "/ultimate", "⚡ Command Center", True),
    ("team", "/ultimate/team", "👥 Team", True),
    ("cases", "/ultimate/cases", "🚨 Fälle", False),
    ("roblox", "/ultimate/roblox", "🎮 Roblox", False),
    ("departments", "/ultimate/departments", "🏢 Abteilungen", False),
    ("calendar", "/ultimate/calendar", "🗓 Kalender", True),
    ("polls", "/ultimate/polls", "🗳 Abstimmungen", True),
    ("feedback", "/ultimate/feedback", "💬 Feedback", True),
    ("handover", "/ultimate/handovers", "🧭 Übergaben", True),
    ("awards", "/ultimate/awards", "🏅 Awards", False),
    ("automations", "/ultimate/automations", "🤖 Automationen", False),
    ("workflows", "/ultimate/workflows", "🧩 Workflows", False),
    ("approvals", "/ultimate/approvals", "✅ Freigaben", False),
    ("announcements", "/ultimate/announcements", "📢 Team-News", True),
    ("ideas", "/ultimate/ideas", "💡 Ideen", True),
    ("goals", "/ultimate/goals", "🎯 Ziele", True),
    ("analytics", "/ultimate/analytics", "📊 Analytics", False),
    ("scoreboard", "/ultimate/scoreboard", "🏆 Team-Score", False),
    ("support", "/ultimate/support", "🎫 SLA & Support", False),
    ("tasks2", "/ultimate/tasks", "📋 Aufgaben 2.0", True),
    ("onboarding", "/ultimate/onboarding", "🧑‍💼 On/Offboarding", False),
    ("orgchart", "/ultimate/orgchart", "🏢 Organigramm", True),
    ("reports", "/ultimate/reports", "📑 Berichte", False),
    ("backup", "/ultimate/backup", "💾 Backup", False),
    ("permissions", "/ultimate/permissions", "🔐 Rechte", False),
    ("security", "/ultimate/security", "🛡 Sicherheit", False),
    ("system", "/ultimate/system", "🩺 System", False),
    ("copilot", "/ultimate/copilot", "🧠 Pulse Copilot", True),
    ("system_twin", "/ultimate/system-twin", "🪞 System-Zwilling", False),
    ("simulator", "/ultimate/simulator", "🧪 Was-wäre-wenn", False),
    ("sandbox", "/ultimate/sandbox", "🧫 Release-Sandbox", False),
)

SUITE_NAV = (
    ("suite", "/suite", "⌂ Führungs-Übersicht", False),
    ("team", "/suite/team", "👥 Teamakten", False),
    ("time", "/suite/time", "⏱ Dienstzeit", True),
    ("schedule", "/suite/schedule", "🗓 Dienstplan", True),
    ("myschedule", "/suite/my-schedule", "👤 Mein Dienstplan", True),
    ("calendar", "/suite/calendar", "📅 Kalender", True),
    ("apps", "/suite/applications", "📝 Recruiting", False),
    ("interviews", "/suite/interviews", "🎤 Gespräche", False),
    ("training", "/suite/training", "🎓 Schulungen", False),
    ("tasks", "/suite/tasks", "📋 Aufgaben", True),
    ("news", "/suite/news", "📢 Team-News", True),
    ("audit", "/suite/audit", "🛡 Audit", False),
    ("search", "/suite/search", "⌕ Suche", True),
    ("cases", "/suite/cases", "🚨 Meldungen", False),
    ("ach", "/suite/achievements", "🏆 Erfolge", False),
)


def visible_nav(items, is_leader: bool):
    return [item for item in items if is_leader or item[3]]


# Component styles of the former Ultimate/Suite shells, expressed with the shared tokens
# (--surface, --surface2, --border, --text, --muted, --p, --p2) so they follow light/dark.
LEGACY_CSS = r"""
<style>
.pulse-legacy{color:var(--text)}
.pulse-legacy h1{font-size:26px;font-weight:850;letter-spacing:-.02em;margin:0}
.pulse-legacy h2{font-size:17px;font-weight:800;margin:0 0 10px}
.pulse-legacy h3{font-size:14px;font-weight:800;margin:0 0 10px}
.pulse-legacy .muted,.pulse-legacy .tiny{color:var(--muted)}.pulse-legacy .tiny{font-size:11px}
.pulse-legacy .kicker{font-size:10px;text-transform:uppercase;letter-spacing:.16em;font-weight:800;color:var(--muted);margin-bottom:5px}
.pulse-legacy .grid{display:grid;gap:14px}.pulse-legacy .g4{grid-template-columns:repeat(4,minmax(0,1fr))}.pulse-legacy .g3{grid-template-columns:repeat(3,minmax(0,1fr))}.pulse-legacy .g2{grid-template-columns:repeat(2,minmax(0,1fr))}
.pulse-legacy .card{background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:17px;box-shadow:0 7px 26px rgba(15,23,42,.045);min-width:0}
.pulse-legacy .hero{border:1px solid rgba(99,102,241,.18);border-radius:22px;padding:22px;margin-bottom:14px;background:linear-gradient(135deg,rgba(91,92,240,.12),rgba(124,58,237,.06)),var(--surface)}
.pulse-legacy .metric{font-size:28px;font-weight:850;letter-spacing:-.02em;margin:6px 0}
.pulse-legacy .row{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--border);border-radius:13px;background:var(--surface2);margin:7px 0}
.pulse-legacy .badge,.pulse-legacy .pill{display:inline-flex;align-items:center;gap:5px;border-radius:999px;border:1px solid var(--border);padding:4px 8px;font-size:10px;font-weight:800;background:var(--surface2)}
.pulse-legacy .ok{color:#079455;background:rgba(18,183,106,.08);border-color:rgba(18,183,106,.2)}.pulse-legacy .warn{color:#b54708;background:rgba(247,144,9,.09);border-color:rgba(247,144,9,.22)}.pulse-legacy .bad{color:#d92d20;background:rgba(240,68,56,.08);border-color:rgba(240,68,56,.22)}.pulse-legacy .blue{color:#4f46e5;background:rgba(91,92,240,.08);border-color:rgba(91,92,240,.2)}
.dark .pulse-legacy .ok{color:#75e2b0}.dark .pulse-legacy .warn{color:#fdba74}.dark .pulse-legacy .bad{color:#ff9d96}.dark .pulse-legacy .blue{color:#a8abff}
.pulse-legacy .btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;border:1px solid var(--border);background:var(--surface2);color:var(--text);border-radius:12px;padding:9px 12px;font-weight:750;font-size:12px;cursor:pointer;transition:.17s}
.pulse-legacy .btn:hover{transform:translateY(-1px);border-color:rgba(91,92,240,.45)}
.pulse-legacy .primary{background:linear-gradient(135deg,var(--p),var(--p2));border-color:transparent;color:#fff}
.pulse-legacy .danger{background:rgba(240,68,56,.1);color:#d92d20;border-color:rgba(240,68,56,.25)}.dark .pulse-legacy .danger{color:#ff9d96}
.pulse-legacy .input,.pulse-legacy .select,.pulse-legacy .ta{width:100%;padding:10px 12px;border-radius:12px;border:1px solid var(--border);background:var(--surface2);color:var(--text);outline:none;font-size:13px}
.pulse-legacy .input:focus,.pulse-legacy .select:focus,.pulse-legacy .ta:focus{border-color:#9296f6;box-shadow:0 0 0 4px rgba(91,92,240,.08)}
.pulse-legacy .ta{min-height:110px;resize:vertical}
.pulse-legacy .form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.pulse-legacy .span2{grid-column:span 2}
.pulse-legacy .table{width:100%;border-collapse:separate;border-spacing:0}.pulse-legacy .table th,.pulse-legacy .table td{padding:10px;border-bottom:1px solid var(--border);text-align:left;font-size:12px}.pulse-legacy .table th{color:var(--muted);text-transform:uppercase;letter-spacing:.08em;font-size:9px}
.pulse-legacy .scroll{overflow:auto}
.pulse-legacy .progress{height:8px;background:var(--surface2);border:1px solid var(--border);border-radius:999px;overflow:hidden}.pulse-legacy .progress>div{height:100%;background:linear-gradient(90deg,var(--p),var(--p2))}
.pulse-legacy .kan{display:grid;grid-template-columns:repeat(5,250px);gap:11px;overflow:auto;padding-bottom:2px}.pulse-legacy .col{background:var(--surface2);border:1px solid var(--border);border-radius:15px;padding:11px;min-height:250px}
.pulse-legacy .notice{border:1px solid var(--border);border-radius:13px;padding:12px;background:var(--surface2);margin:8px 0}
.pulse-legacy .timeline{padding-left:19px;border-left:1px solid var(--border)}.pulse-legacy .event{margin-bottom:15px}
.pulse-legacy a:not(.btn):not(.row):not(.result){color:inherit}
.pulse-legacy-head{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:18px}
.pulse-legacy-head .actions{display:flex;flex-wrap:wrap;gap:8px}
.pulse-search-overlay{position:fixed;inset:0;background:rgba(2,6,23,.55);backdrop-filter:blur(6px);display:none;align-items:flex-start;justify-content:center;padding:10vh 16px;z-index:80}
.pulse-search-overlay>div{width:min(860px,100%);background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:14px;box-shadow:0 30px 90px rgba(0,0,0,.3)}
.pulse-search-overlay .result{display:block;padding:11px 12px;border-radius:11px}.pulse-search-overlay .result:hover{background:var(--surface2)}
@media(max-width:1100px){.pulse-legacy .g4{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:800px){.pulse-legacy .g3,.pulse-legacy .g2,.pulse-legacy .form{grid-template-columns:1fr}.pulse-legacy .span2{grid-column:auto}.pulse-legacy .row{flex-wrap:wrap}.pulse-legacy .card{overflow-x:auto}.pulse-legacy h1{font-size:22px}.pulse-legacy .input,.pulse-legacy .select,.pulse-legacy .ta{font-size:16px}.pulse-legacy .kan{grid-template-columns:repeat(5,220px)}}
</style>
"""


def legacy_page(ws, ctx, nav_key: str, title: str, subtitle: str, body: str,
                actions: str = "", scripts: str = "", kicker: str = "Pulse TeamOS"):
    """Render a former Ultimate/Suite page inside the shared Pulse shell."""
    from fastapi.responses import HTMLResponse
    from pulse_pro import render_pro_page

    e = ws.esc
    head = (
        f'<div class="pulse-legacy-head"><div><div class="pulse-kicker" style="opacity:1;color:var(--muted)">{e(kicker)}</div>'
        f'<h1>{e(title)}</h1><div class="muted text-sm mt-1">{e(subtitle)}</div></div>'
        f'<div class="actions">{actions}</div></div>'
    )
    content = f'<div class="pulse-legacy">{head}{body}</div>{scripts}'
    return HTMLResponse(render_pro_page(ws, title, ctx, nav_key, content, LEGACY_CSS))
