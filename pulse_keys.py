"""Audit #67: keyboard operation of the dashboard - shortcut help (``?``) and two-key navigation (``g`` then a letter).

Complements the existing skip link and the command palette (Ctrl+K). Shortcuts are ignored while typing in a field,
never with Ctrl/Alt/Meta pressed (so browser shortcuts keep working) and only *navigate* - destinations still check
permissions themselves. The dialog is a normal, focus-managed ``role="dialog"`` that closes with Esc.
"""
from __future__ import annotations

NAV = (("m", "/me", "Mein Pulse"), ("t", "/team", "Team"), ("a", "/tasks", "Aufgaben"), ("i", "/pulse-inbox", "Inbox"), ("s", "/team-status", "Teamstatus"))
GENERAL = (("Strg + K", "Befehlspalette: Seiten und Funktionen suchen"), ("?", "Diese Übersicht öffnen"), ("Esc", "Dialog schließen"),
           ("Tab / Umschalt + Tab", "Zum nächsten / vorherigen Element"), ("Eingabe", "Fokussierten Link oder Button auslösen"))


def button_html() -> str:
    return ('<button type="button" id="pulseKeysOpen" class="pulse-btn ghost" style="margin-left:8px" '
            'aria-haspopup="dialog" aria-controls="pulseKeysDialog">⌨ Tastaturkürzel (?)</button>')


def dialog_html() -> str:
    rows = "".join(f"<tr><th scope=\"row\"><kbd>{k}</kbd></th><td>{t}</td></tr>" for k, t in GENERAL)
    rows += "".join(f"<tr><th scope=\"row\"><kbd>g</kbd> dann <kbd>{k}</kbd></th><td>Gehe zu: {label}</td></tr>" for k, _url, label in NAV)
    mapping = ",".join(f"'{k}':'{url}'" for k, url, _label in NAV)
    script = ("<script>(function(){const d=document.getElementById('pulseKeysDialog'),b=document.getElementById('pulseKeysOpen');if(!d)return;"
              "let seq=false,t=null,last=null;"
              f"const go={{{mapping}}};"
              "const typing=e=>e&&(/^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName)||e.isContentEditable);"
              "function open(){last=document.activeElement;d.style.display='flex';const c=d.querySelector('button');if(c)c.focus();}"
              "function close(){d.style.display='none';if(last&&last.focus)last.focus();}"
              "if(b)b.addEventListener('click',open);d.querySelector('button').addEventListener('click',close);"
              "d.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();close();}"
              "if(e.key==='Tab'){e.preventDefault();d.querySelector('button').focus();}});"
              "document.addEventListener('keydown',e=>{if(e.ctrlKey||e.metaKey||e.altKey||typing(document.activeElement))return;"
              "if(e.key==='?'){e.preventDefault();open();return;}"
              "if(seq&&go[e.key]){e.preventDefault();seq=false;location.href=go[e.key];return;}"
              "if(e.key==='g'){seq=true;clearTimeout(t);t=setTimeout(()=>{seq=false;},1200);return;}seq=false;});})();</script>")
    return ('<div id="pulseKeysDialog" role="dialog" aria-modal="true" aria-labelledby="pulseKeysTitle" '
            'style="display:none;position:fixed;inset:0;z-index:90;background:rgba(0,0,0,.55);align-items:center;justify-content:center;padding:16px">'
            '<div class="pulse-card" style="max-width:480px;width:100%;max-height:90vh;overflow:auto"><div class="pulse-card-h">'
            '<div class="pulse-section-title" id="pulseKeysTitle">Tastaturkürzel</div>'
            '<button type="button" class="pulse-btn ghost" aria-label="Dialog schließen">✕</button></div>'
            f'<div class="pulse-card-b"><table class="pulse-table"><tbody>{rows}</tbody></table>'
            '<p class="text-[11px] text-slate-400 mt-2">Kürzel gelten nicht in Eingabefeldern. Du landest nur auf Seiten, für die du berechtigt bist.</p></div></div></div>' + script)
