"""Interaktive, überspringbare Ersteinführung für das Pulse-Team-Dashboard.

The tour is entirely client-side: it stores only the completion flag in the current browser,
scoped by the authenticated Discord user id rendered by the shared shell. It never sends or
changes team data.
"""


def assets() -> str:
    """Return the shared-shell CSS, launcher, accessible tour card and browser logic."""
    css = r"""
<style>
#pulseTutorialLauncher{
  position:fixed;right:20px;bottom:20px;z-index:90;display:inline-flex;align-items:center;gap:8px;
  min-height:44px;padding:0 16px;border:1px solid rgba(255,118,92,.35);border-radius:999px;
  background:linear-gradient(135deg,var(--p),var(--p2));color:#fff;font-size:12px;font-weight:850;
  box-shadow:0 12px 32px rgba(0,0,0,.24),0 0 0 4px rgba(255,118,92,.08);cursor:pointer;
  transition:transform .18s ease,box-shadow .18s ease
}
#pulseTutorialLauncher:hover{transform:translateY(-2px);box-shadow:0 16px 36px rgba(0,0,0,.28),0 0 0 5px rgba(255,118,92,.13)}
#pulseTutorialLauncher .pulse-tour-launch-icon{display:grid;place-items:center;width:20px;height:20px;border:1px solid rgba(255,255,255,.55);border-radius:50%;font-size:12px}
#pulseTutorialCard{
  position:fixed;right:20px;bottom:78px;z-index:100;width:min(440px,calc(100vw - 32px));
  max-height:min(72vh,640px);overflow:auto;padding:18px;border:1px solid var(--border);
  border-radius:20px;background:var(--surface);color:var(--text);
  box-shadow:0 26px 90px rgba(0,0,0,.36);overscroll-behavior:contain
}
#pulseTutorialCard[hidden],#pulseTutorialLauncher[hidden]{display:none!important}
#pulseTutorialCard:focus{outline:none}
#pulseTutorialCard .pulse-tour-kicker{font-size:10px;font-weight:850;letter-spacing:.13em;text-transform:uppercase;color:var(--p)}
#pulseTutorialCard h2{font-size:19px;font-weight:900;letter-spacing:-.025em;line-height:1.2;margin:9px 0}
#pulseTutorialCard .pulse-tour-copy{font-size:13px;line-height:1.65;color:var(--muted);margin:0}
#pulseTutorialCard .pulse-tour-progress{height:6px;overflow:hidden;border-radius:99px;background:var(--surface2);border:1px solid var(--border)}
#pulseTutorialCard .pulse-tour-progress>span{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--p),var(--p2));transition:width .22s ease}
#pulseTutorialCard .pulse-tour-actions{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:17px}
#pulseTutorialCard .pulse-tour-btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:42px;padding:0 12px;border:1px solid var(--border);border-radius:11px;background:var(--surface2);color:var(--text);font-size:11px;font-weight:800;cursor:pointer;text-decoration:none}
#pulseTutorialCard .pulse-tour-btn:disabled{opacity:.38;cursor:not-allowed}
#pulseTutorialCard .pulse-tour-btn.primary{background:linear-gradient(135deg,var(--p),var(--p2));border-color:transparent;color:white}
#pulseTutorialCard .pulse-tour-btn.open{margin-left:auto}
#pulseTutorialCard .pulse-tour-close{width:34px;height:34px;display:grid;place-items:center;border-radius:10px;border:1px solid var(--border);background:var(--surface2);color:var(--muted);cursor:pointer}
#pulseTutorialCard .pulse-tour-note{margin-top:12px;padding:9px 11px;border-radius:11px;background:var(--surface2);border:1px solid var(--border);font-size:10px;line-height:1.5;color:var(--muted)}
.pulse-tour-highlight{
  position:relative!important;z-index:55!important;outline:3px solid var(--p3)!important;outline-offset:4px!important;
  box-shadow:0 0 0 8px rgba(255,118,92,.17),0 10px 28px rgba(0,0,0,.16)!important;border-radius:12px!important
}
#pulseTutorialCard button:focus-visible,#pulseTutorialCard a:focus-visible,#pulseTutorialLauncher:focus-visible{
  outline:3px solid var(--p3);outline-offset:3px
}
@media(max-width:767px){
  #pulseTutorialLauncher{right:12px;bottom:12px;min-height:46px}
  #pulseTutorialCard{left:12px;right:12px;bottom:68px;width:auto;max-height:68vh;padding:15px}
  #pulseTutorialCard .pulse-tour-actions{gap:7px}
  #pulseTutorialCard .pulse-tour-btn{flex:1}
  #pulseTutorialCard .pulse-tour-btn.open{margin-left:0;flex-basis:100%}
}
@media(prefers-reduced-motion:reduce){
  #pulseTutorialLauncher,#pulseTutorialCard .pulse-tour-progress>span{transition:none!important}
}
</style>
"""
    markup = r"""
<button id="pulseTutorialLauncher" type="button" aria-haspopup="dialog" aria-controls="pulseTutorialCard" aria-expanded="false">
  <span class="pulse-tour-launch-icon" aria-hidden="true">?</span><span>Einführung</span>
</button>
<section id="pulseTutorialCard" role="dialog" aria-modal="false" aria-labelledby="pulseTutorialTitle"
         aria-describedby="pulseTutorialCopy" tabindex="-1" hidden>
  <div class="flex items-start justify-between gap-3">
    <div class="min-w-0 flex-1">
      <div class="pulse-tour-kicker">Pulse TeamOS · Einführung</div>
      <div class="flex items-center justify-between gap-3 mt-2">
        <span id="pulseTutorialCount" class="text-[10px] font-bold text-slate-400">Schritt 1 von 1</span>
        <button id="pulseTutorialClose" class="pulse-tour-close" type="button" aria-label="Einführung später fortsetzen">✕</button>
      </div>
    </div>
  </div>
  <div class="pulse-tour-progress mt-3" role="progressbar" aria-label="Fortschritt der Einführung" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
    <span id="pulseTutorialProgress"></span>
  </div>
  <h2 id="pulseTutorialTitle">Willkommen bei Pulse</h2>
  <p id="pulseTutorialCopy" class="pulse-tour-copy"></p>
  <div id="pulseTutorialNote" class="pulse-tour-note" hidden></div>
  <div class="pulse-tour-actions">
    <button id="pulseTutorialPrevious" class="pulse-tour-btn" type="button" disabled>← Zurück</button>
    <button id="pulseTutorialSkip" class="pulse-tour-btn" type="button">Überspringen</button>
    <button id="pulseTutorialNext" class="pulse-tour-btn primary" type="button">Weiter →</button>
    <a id="pulseTutorialOpen" class="pulse-tour-btn open" href="/me" target="_blank" rel="noopener noreferrer" hidden>Bereich öffnen ↗</a>
  </div>
</section>
"""
    script = r"""
<script>
(function(){
  if(window.PulseTeamTutorial)return;
  const card=document.getElementById('pulseTutorialCard');
  const launcher=document.getElementById('pulseTutorialLauncher');
  if(!card||!launcher)return;

  const shell=document.querySelector('.pulse-shell');
  const userId=shell&&shell.dataset.pulseUser?shell.dataset.pulseUser:'browser';
  const storageKey='pulse.team-intro.v1:'+userId;
  const snoozeKey='pulse.team-intro.snoozed.v1:'+userId;
  const title=document.getElementById('pulseTutorialTitle');
  const copy=document.getElementById('pulseTutorialCopy');
  const count=document.getElementById('pulseTutorialCount');
  const progress=document.getElementById('pulseTutorialProgress');
  const progressBar=progress&&progress.parentElement;
  const note=document.getElementById('pulseTutorialNote');
  const previous=document.getElementById('pulseTutorialPrevious');
  const next=document.getElementById('pulseTutorialNext');
  const skip=document.getElementById('pulseTutorialSkip');
  const openLink=document.getElementById('pulseTutorialOpen');
  const closeButton=document.getElementById('pulseTutorialClose');

  const steps=[
    {
      title:'Willkommen bei Pulse TeamOS!',
      text:'Diese kurze Tour zeigt dir die wichtigsten Bereiche deiner Teamverwaltung. Du kannst jederzeit weiterspringen, die Einführung überspringen oder später erneut über den Knopf „Einführung“ starten.',
      note:'Die Tour ist auf deine sichtbaren Berechtigungen abgestimmt. Sie führt dich nur zu Menüpunkten, die dein Konto auch sehen darf.',
      open:'/me'
    },
    {
      title:'So ist das Dashboard aufgebaut',
      text:'Links findest du die Navigation, sortiert nach Themen wie Team, Support, Recruiting und Planung. Bereiche lassen sich aufklappen. Menüpunkte, für die dir Rechte fehlen, werden ausgeblendet.',
      selector:'#pulseSidebar nav',
      open:'/me'
    },
    {
      title:'Mein Pulse – dein persönlicher Startpunkt',
      text:'Hier findest du deine Schicht, Wochenziele, eigene Aufgaben und Tickets, persönliche Hinweise sowie anstehende Besprechungen. Von hier aus startest du typischerweise in deinen Arbeitstag.',
      selector:'#pulseSidebar a[href="/me"]',
      open:'/me'
    },
    {
      title:'Übersicht – das Team im Blick',
      text:'Die Übersicht fasst wichtige Kennzahlen und aktuelle Vorgänge zusammen. Nutze sie, um schnell zu erkennen, was gerade Aufmerksamkeit braucht.',
      selector:'#pulseSidebar a[href="/dashboard"]',
      open:'/dashboard'
    },
    {
      title:'Inbox – persönliche Hinweise',
      text:'In der Inbox landen Benachrichtigungen und Hinweise zu Vorgängen, die dich betreffen. Öffne neue Einträge, um zum jeweiligen Bereich zu gelangen, und markiere erledigte Hinweise als gelesen.',
      selector:'#pulseSidebar a[href="/pulse-inbox"]',
      open:'/pulse-inbox'
    },
    {
      title:'Team – Teammitglieder und Status',
      text:'Die Teamliste hilft dir, Mitglieder und ihre Teamdaten zu finden. Über den Teamstatus erkennst du – soweit freigegeben – Verfügbarkeit und aktuelle Dienstsituationen.',
      selector:'#pulseSidebar a[href="/team"]',
      open:'/team'
    },
    {
      title:'Tickets – Support bearbeiten',
      text:'Hier bearbeitest du eingehende Supportanfragen. Prüfe neue Tickets, übernimm zuständige Vorgänge und halte den Bearbeitungsstand aktuell. Für Sprach-Support gibt es einen eigenen Menüpunkt, sofern du Zugriff hast.',
      selector:'#pulseSidebar a[href="/tickets"]',
      open:'/tickets'
    },
    {
      title:'Bewerbungen – Recruiting',
      text:'Im Bewerbungsbereich findest du eingegangene Bewerbungen und ihren Bearbeitungsstand. Je nach Rolle kannst du Bewerbungen prüfen, Gespräche organisieren und Entscheidungen dokumentieren.',
      selector:'#pulseSidebar a[href="/applications"]',
      open:'/applications'
    },
    {
      title:'Planung – Termine und Abwesenheiten',
      text:'Kalender, Meetings, Abmeldungen und Schulungen helfen dabei, die Teamarbeit zu organisieren. Prüfe Termine und halte Abwesenheiten beziehungsweise Besprechungsergebnisse aktuell.',
      selector:'#pulseSidebar a[href="/calendar"]',
      open:'/calendar'
    },
    {
      title:'Team-Wiki – Wissen an einem Ort',
      text:'Im Team-Wiki liegen wichtige Anleitungen und interne Informationen. Schau zuerst dort nach, wenn du bei einem Ablauf nicht sicher bist. Schulungen findest du zusätzlich unter dem entsprechenden Menüpunkt.',
      selector:'#pulseSidebar a[href="/wiki"]',
      open:'/wiki'
    },
    {
      title:'Weitere Funktionen',
      text:'Unter „Erweiterte Funktionen“ findest du – abhängig von deinen Rechten – unter anderem Command Center, Abstimmungen, Feedback, Aufgaben und Operations Suite. Klappe den Bereich auf, um die verfügbaren Module zu entdecken.',
      selector:'#pulseSidebar .pulse-advanced-group',
      open:'/ultimate'
    },
    {
      title:'Schnell suchen statt lange klicken',
      text:'Über „Schnell suchen…“ findest du Seiten und Funktionen. Noch schneller geht es mit Strg+K (auf dem Mac: Cmd+K). Die Suche zeigt nur Bereiche, die für dich freigegeben sind.',
      selector:'#pulseSidebar button[onclick*="pulseTogglePalette"]'
    },
    {
      title:'Fertig – du kannst loslegen!',
      text:'Das waren die wichtigsten Grundlagen. Du kannst die Einführung jederzeit über den Knopf unten rechts erneut öffnen. Viel Erfolg bei deiner Arbeit im Team!',
      note:'Tipp: Wenn du etwas nicht sehen oder öffnen kannst, fehlt dir möglicherweise die passende Berechtigung. Wende dich dann an die zuständige Führungskraft.'
    }
  ];

  const available=steps.filter(step=>!step.selector||document.querySelector(step.selector));
  let index=0,activeTarget=null,restoreFocus=null,openedMobileNav=false,collapsedSidebar=false;
  let completed=false;

  function readFlag(key){
    try{return localStorage.getItem(key)==='1'}catch(_){return false}
  }
  function writeFlag(key,value){
    try{if(value)localStorage.setItem(key,'1');else localStorage.removeItem(key)}catch(_){}
  }
  function readSessionFlag(key){
    try{return sessionStorage.getItem(key)==='1'}catch(_){return false}
  }
  function writeSessionFlag(key){
    try{sessionStorage.setItem(key,'1')}catch(_){}
  }
  function openAncestors(element){
    let details=element&&element.closest('details');
    while(details){
      details.open=true;
      details=details.parentElement?details.parentElement.closest('details'):null;
    }
  }
  function clearTarget(){
    if(activeTarget)activeTarget.classList.remove('pulse-tour-highlight');
    activeTarget=null;
  }
  function closeMobileNavIfNeeded(){
    if(openedMobileNav&&typeof window.pulseCloseNav==='function')window.pulseCloseNav();
    openedMobileNav=false;
  }
  function setLauncher(open){
    launcher.setAttribute('aria-expanded',String(open));
  }
  function closeTour(permanentSnooze){
    clearTarget();
    card.hidden=true;
    setLauncher(false);
    if(permanentSnooze)writeSessionFlag(snoozeKey);
    closeMobileNavIfNeeded();
    if(restoreFocus&&typeof restoreFocus.focus==='function')restoreFocus.focus();
    restoreFocus=null;
  }
  function completeTour(){
    completed=true;
    writeFlag(storageKey,true);
    writeSessionFlag(snoozeKey);
    closeTour(false);
  }
  function findOpenLink(step,target){
    if(target){
      if(target.matches&&target.matches('a[href]'))return target.getAttribute('href');
      const nested=target.querySelector&&target.querySelector('a[href]');
      if(nested)return nested.getAttribute('href');
    }
    return step.open||'';
  }
  function renderStep(){
    clearTarget();
    const step=available[index];
    if(!step){completeTour();return}
    if(window.innerWidth<768){
      const sidebar=document.getElementById('pulseSidebar');
      if(sidebar&&sidebar.classList.contains('hidden')&&typeof window.pulseOpenNav==='function'){
        window.pulseOpenNav();
        openedMobileNav=true;
      }
    }
    if(step.selector){
      const target=document.querySelector(step.selector);
      if(target){
        openAncestors(target);
        target.classList.add('pulse-tour-highlight');
        activeTarget=target;
        try{target.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'nearest',inline:'nearest'})}catch(_){}
      }
    }
    title.textContent=step.title;
    copy.textContent=step.text;
    note.hidden=!step.note;
    note.textContent=step.note||'';
    count.textContent='Schritt '+(index+1)+' von '+available.length;
    const percent=Math.round(((index+1)/available.length)*100);
    progress.style.width=percent+'%';
    if(progressBar){
      progressBar.setAttribute('aria-valuenow',String(percent));
    }
    previous.disabled=index===0;
    next.textContent=index===available.length-1?'Einführung abschließen ✓':'Weiter →';
    skip.hidden=index===available.length-1;
    const href=findOpenLink(step,activeTarget);
    if(href&&href.startsWith('/')&&!href.startsWith('//')){
      openLink.href=href;
      openLink.hidden=false;
    }else{
      openLink.hidden=true;
    }
  }
  function start(){
    restoreFocus=document.activeElement;
    completed=false;
    index=0;
    card.hidden=false;
    setLauncher(true);
    const sidebar=document.getElementById('pulseSidebar');
    collapsedSidebar=!!(sidebar&&sidebar.classList.contains('pulse-sidebar-collapsed'));
    if(collapsedSidebar)sidebar.classList.remove('pulse-sidebar-collapsed');
    renderStep();
    next.focus();
  }
  function stepForward(){
    if(index>=available.length-1){completeTour();return}
    index++;
    renderStep();
  }
  function stepBack(){
    if(index<=0)return;
    index--;
    renderStep();
  }

  launcher.addEventListener('click',()=>{
    if(!card.hidden){closeTour(true);return}
    start();
  });
  previous.addEventListener('click',stepBack);
  next.addEventListener('click',stepForward);
  skip.addEventListener('click',completeTour);
  closeButton.addEventListener('click',()=>closeTour(true));
  card.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();closeTour(true);return}
    if(event.key==='ArrowRight'&&!next.disabled){event.preventDefault();stepForward();return}
    if(event.key==='ArrowLeft'&&!previous.disabled){event.preventDefault();stepBack();return}
    if(event.key==='Tab'){
      const items=Array.from(card.querySelectorAll('button:not([disabled]):not([hidden]),a[href]:not([hidden])'));
      if(!items.length)return;
      const first=items[0],last=items[items.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
    }
  });

  window.PulseTeamTutorial={
    start:start,
    skip:completeTour,
    isCompleted:()=>readFlag(storageKey),
    steps:()=>available.length
  };

  function init(){
    launcher.hidden=false;
    if(!readFlag(storageKey)&&!readSessionFlag(snoozeKey)){
      window.setTimeout(start,450);
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();
})();
</script>
"""
    return css + markup + script
