/* Public website: sparks above the headframe and, only when optional services exist, consent. */
(function () {
  "use strict";

  function startSparks() {
    var canvas = document.querySelector(".sparks");
    if (!canvas || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var context = canvas.getContext("2d");
    if (!context) return;
    var sparks = [];
    var visible = true;
    var running = false;

    function size() {
      canvas.width = canvas.clientWidth;
      canvas.height = canvas.clientHeight;
    }

    function spawn() {
      return {
        x: canvas.width * (0.55 + Math.random() * 0.4),
        y: canvas.height + Math.random() * 60,
        speed: 0.25 + Math.random() * 0.7,
        drift: (Math.random() - 0.5) * 0.35,
        life: 0.35 + Math.random() * 0.6,
        radius: 0.7 + Math.random() * 1.5
      };
    }

    function frame() {
      if (!visible || document.hidden) {
        running = false;
        return;
      }
      context.clearRect(0, 0, canvas.width, canvas.height);
      while (sparks.length < 36) sparks.push(spawn());
      sparks.forEach(function (spark, index) {
        spark.y -= spark.speed;
        spark.x += spark.drift;
        spark.life -= 0.0016;
        if (spark.life <= 0 || spark.y < 0) {
          sparks[index] = spawn();
          return;
        }
        context.globalAlpha = Math.min(1, spark.life * 1.4);
        context.fillStyle = "#ff8a6b";
        context.beginPath();
        context.arc(spark.x, spark.y, spark.radius, 0, Math.PI * 2);
        context.fill();
      });
      requestAnimationFrame(frame);
    }

    function resume() {
      if (!running && visible && !document.hidden) {
        running = true;
        requestAnimationFrame(frame);
      }
    }

    size();
    addEventListener("resize", size);
    document.addEventListener("visibilitychange", resume);
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      resume();
    }).observe(canvas);
    resume();
  }

  var KEY = "pulse.consent";

  function readConsent(version) {
    try {
      var saved = JSON.parse(localStorage.getItem(KEY) || "null");
      return saved && saved.version === version ? saved : null;
    } catch (_) {
      return null;
    }
  }

  function startConsent() {
    var source = document.getElementById("consent-config");
    if (!source) return;
    var config = JSON.parse(source.textContent);

    function activate(choices) {
      config.services.forEach(function (service) {
        if (!choices[service.id]) return;
        document.querySelectorAll('template[data-consent-service="' + service.id + '"]').forEach(function (template) {
          template.replaceWith(template.content.cloneNode(true));
        });
      });
    }

    function save(choices) {
      try {
        localStorage.setItem(KEY, JSON.stringify({ version: config.version, at: new Date().toISOString(), choices: choices }));
      } catch (_) {
        /* The choice then applies to this page view only. */
      }
      var banner = document.querySelector(".consent");
      if (banner) banner.remove();
      activate(choices);
    }

    function all(value) {
      var choices = {};
      config.services.forEach(function (service) { choices[service.id] = value; });
      return choices;
    }

    function button(label, action) {
      var element = document.createElement("button");
      element.type = "button";
      element.className = "btn btn-line";
      element.textContent = label;
      element.addEventListener("click", action);
      return element;
    }

    function show(detail) {
      var old = document.querySelector(".consent");
      if (old) old.remove();
      var saved = readConsent(config.version);
      var banner = document.createElement("section");
      banner.className = "consent";
      banner.setAttribute("aria-label", "Optionale Dienste");
      var text = document.createElement("p");
      text.textContent = "Diese Seite kann optionale Dienste laden. Sie laufen nur, wenn du zustimmst.";
      banner.appendChild(text);
      var boxes = {};
      if (detail) {
        config.services.forEach(function (service) {
          var label = document.createElement("label");
          var box = document.createElement("input");
          box.type = "checkbox";
          box.checked = !!(saved && saved.choices[service.id]);
          boxes[service.id] = box;
          label.appendChild(box);
          label.appendChild(document.createTextNode(" " + service.label + ": " + service.purpose + " (" + service.provider + ")"));
          banner.appendChild(label);
        });
      }
      var row = document.createElement("div");
      row.className = "cta";
      row.appendChild(button("Alle akzeptieren", function () { save(all(true)); }));
      row.appendChild(button("Nur notwendige", function () { save(all(false)); }));
      row.appendChild(detail
        ? button("Auswahl speichern", function () {
            var choices = {};
            Object.keys(boxes).forEach(function (id) { choices[id] = boxes[id].checked; });
            save(choices);
          })
        : button("Einstellungen", function () { show(true); }));
      banner.appendChild(row);
      document.body.appendChild(banner);
    }

    document.querySelectorAll("[data-consent-open]").forEach(function (element) {
      element.addEventListener("click", function () { show(true); });
    });
    var current = readConsent(config.version);
    if (current) activate(current.choices);
    else show(false);
  }

  startSparks();
  startConsent();
})();
