"""Audit #141: Ablaufdiagramm einer Automation (Auslöser → Bedingung → Freigabe → Aktionen) und Reihenfolge-Werkzeug.

Reine Darstellung: Es wird nichts gespeichert und nichts ausgeführt. Alle Texte stammen aus den festen Listen der Regeln
(``pulse_automation_rules``) oder werden maskiert. Das Diagramm funktioniert ohne Skript (CSS-Pfeile); das kleine Skript
zum Verschieben der Aktionen im Editor ist eine reine Komfortfunktion, das Speichern bleibt ein normales Formular.
"""
from __future__ import annotations

import pulse_automation_rules as rules

STYLE = ("<style>.wf-flow{display:flex;flex-wrap:wrap;align-items:stretch;gap:6px}.wf-node{border:1px solid var(--border,#444);border-radius:10px;"
         "padding:8px 10px;font-size:12px;min-width:130px;max-width:260px}.wf-node b{display:block;font-size:10px;letter-spacing:.04em;opacity:.7}"
         ".wf-arrow{align-self:center;opacity:.6}.wf-trigger{border-color:#6366f1}.wf-cond{border-style:dashed}.wf-gate{border-color:#f59e0b}"
         ".wf-step{border-color:#10b981}.wf-note{font-size:11px;opacity:.75;margin-top:4px}</style>")


def nodes(automation: dict) -> list[dict]:
    """Strukturierte Knoten ``{kind, label, text}`` - testbar ohne HTML."""
    cfg = automation.get("trigger_config") or {}
    trigger = automation.get("trigger_type")
    label = rules.TRIGGERS.get(trigger, str(trigger or "?"))
    detail = ""
    if trigger == "interval":
        detail = f"alle {automation.get('interval_minutes') or 1440} Minuten"
    elif trigger == "domain_event":
        detail = f"{cfg.get('domain', '?')} · {cfg.get('action', '*')}"
    elif trigger == "antinuke_event":
        detail = ", ".join(cfg.get("events") or [])
    out = [{"kind": "trigger", "label": "WENN", "text": label + (f" ({detail})" if detail else "")}]
    depends = cfg.get("depends_on") or {}
    if depends.get("automation_id"):
        out.append({"kind": "cond", "label": "ABHÄNGIG", "text": f"nach Automation {depends['automation_id']} (≤ {int(depends.get('max_age_minutes') or 0)} Min.)"})
    condition = automation.get("condition") or {}
    if condition.get("type") and condition.get("type") != "always":
        out.append({"kind": "cond", "label": "UND", "text": rules.CONDITIONS.get(condition["type"], condition["type"]).replace("Wert", str(condition.get("value", 0)))})
    for index, stage in enumerate((automation.get("approval") or {}).get("stages") or [], 1):
        out.append({"kind": "gate", "label": f"FREIGABE {index}", "text": f"{stage.get('label') or rules.APPROVER_ROLES.get(stage.get('role'), '?')} ({int(stage.get('min_approvals', 1))}×)"})
    steps = automation.get("steps") or []
    if not steps and automation.get("action_type"):
        steps = [{"type": automation["action_type"]}]
    for index, step in enumerate(steps, 1):
        out.append({"kind": "step", "label": f"DANN {index}", "text": rules.STEP_TYPES.get(step.get("type"), str(step.get("type") or "?"))})
    return out


def render(automation: dict, esc) -> str:
    parts = []
    for i, node in enumerate(nodes(automation)):
        if i:
            parts.append('<span class="wf-arrow" aria-hidden="true">→</span>')
        parts.append(f'<div class="wf-node wf-{esc(node["kind"])}"><b>{esc(node["label"])}</b>{esc(node["text"])}</div>')
    notes = []
    retry = int((automation.get("retry") or {}).get("max_attempts") or 0)
    if retry:
        notes.append(f"Fehlgeschlagene Aktionen werden bis zu {retry}× wiederholt.")
    if automation.get("test_mode"):
        notes.append("Testmodus: Aktionen werden nur protokolliert.")
    note_html = f'<div class="wf-note">{esc(" ".join(notes))}</div>' if notes else ""
    return f'{STYLE}<div class="wf-flow" role="img" aria-label="Ablauf der Automation">{"".join(parts)}</div>{note_html}'


# Verschiebt die Felder zweier Aktionszeilen (stepN_*) - nur Komfort; gespeichert wird weiterhin das normale Formular.
MOVE_SCRIPT = ("<script>(function(){function rows(){return Array.from(document.querySelectorAll('[data-step-row]'));}"
               "function swap(a,b){a.querySelectorAll('input,select').forEach(function(x){var n=x.name.replace(/^step\\d+_/,'');"
               "var y=b.querySelector('[name$=\"_'+n+'\"]');if(!y)return;if(x.type==='checkbox'){var t=x.checked;x.checked=y.checked;y.checked=t;}"
               "else{var v=x.value;x.value=y.value;y.value=v;}});}"
               "document.addEventListener('click',function(e){var b=e.target.closest('[data-step-move]');if(!b)return;e.preventDefault();"
               "var r=rows(),i=r.indexOf(b.closest('[data-step-row]')),j=i+(b.dataset.stepMove==='up'?-1:1);if(j<0||j>=r.length)return;swap(r[i],r[j]);});})();</script>")


def move_buttons(index: int) -> str:
    return (f'<span class="tiny"><button type="button" class="btn" data-step-move="up" aria-label="Aktion {index} nach oben">↑</button> '
            f'<button type="button" class="btn" data-step-move="down" aria-label="Aktion {index} nach unten">↓</button></span>')
