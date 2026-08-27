/* Progressive enhancement for the Jinja app.
 *
 *   1. inline field saves
 *   2. evidence rail
 *   3. run progress (SSE)
 *   4. theme toggle
 *   5. approve dialog
 *   6. attention group toggles
 */

(function () {
  "use strict";

  var EASE = "cubic-bezier(.4,0,.2,1)";
  var THEME_KEY = "csf-theme";
  var lastFocus = null;

  function flash(element) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    element.animate(
      [{ opacity: 0.5 }, { opacity: 1 }],
      { duration: 200, easing: EASE }
    );
  }

  /* --- theme -------------------------------------------------------------- */

  function currentTheme() {
    if (document.documentElement.classList.contains("dark")) return "dark";
    if (document.documentElement.classList.contains("light")) return "light";
    try {
      var stored = localStorage.getItem(THEME_KEY);
      if (stored === "dark" || stored === "light") return stored;
    } catch (e) {}
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function applyTheme(mode) {
    var root = document.documentElement;
    root.classList.remove("dark", "light");
    root.classList.add(mode === "dark" ? "dark" : "light");
    try {
      localStorage.setItem(THEME_KEY, mode);
    } catch (e) {}
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.textContent = mode === "dark" ? "Light" : "Dark";
      btn.setAttribute("aria-pressed", mode === "dark" ? "true" : "false");
    }
  }

  function toggleTheme() {
    applyTheme(currentTheme() === "dark" ? "light" : "dark");
  }

  /* --- 1. inline field saves -------------------------------------------- */

  function swap(target, html) {
    var holder = document.createElement("div");
    holder.innerHTML = html.trim();
    var replacement = holder.firstElementChild;
    if (!replacement) return;
    target.replaceWith(replacement);
    flash(replacement);
  }

  async function saveField(form, event) {
    event.preventDefault();

    var selector = form.getAttribute("hx-target");
    var target = selector ? document.querySelector(selector) : form;
    if (!target) return;

    var button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;

    try {
      var response = await fetch(form.getAttribute("hx-post"), {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "fetch" },
      });
      if (!response.ok) throw new Error(response.statusText);
      swap(target, await response.text());
    } catch (error) {
      if (button) button.disabled = false;
      console.error("save failed, falling back to a full post", error);
      form.removeAttribute("hx-post");
      form.submit();
    }
  }

  /* --- 2. the evidence rail --------------------------------------------- */

  function railParts() {
    return {
      body: document.getElementById("rail-body"),
      title: document.getElementById("rail-title"),
      meta: document.getElementById("rail-meta"),
    };
  }

  async function openCitation(link, event) {
    var rail = railParts();
    if (!rail.body) return;

    event.preventDefault();

    var claimId = link.dataset.cite;
    var docId = link.dataset.doc;

    document.querySelectorAll(".cite[aria-current]").forEach(function (chip) {
      chip.removeAttribute("aria-current");
    });
    link.setAttribute("aria-current", "true");

    rail.title.textContent = docId;
    rail.meta.textContent = "loading";

    try {
      var url =
        "/runs/" +
        link.dataset.thread +
        "/source/" +
        docId +
        "?claim=" +
        encodeURIComponent(claimId);
      var response = await fetch(url, {
        headers: { "X-Requested-With": "fetch" },
      });
      if (!response.ok) throw new Error(response.statusText);

      rail.body.innerHTML = await response.text();
      rail.meta.textContent = claimId;
      flash(rail.body);

      var first = rail.body.querySelector("[data-cited]");
      if (first) first.scrollIntoView({ block: "nearest" });
    } catch (error) {
      console.error("could not load the source", error);
      rail.meta.textContent = "";
      window.location.href = link.href;
    }
  }

  function tagCitationsWithThread() {
    var progress = document.getElementById("progress");
    var match = window.location.pathname.match(/^\/runs\/([^/]+)/);
    var thread = match ? match[1] : progress && progress.dataset.thread;
    if (!thread) return;
    document.querySelectorAll(".cite[data-doc]").forEach(function (chip) {
      chip.dataset.thread = thread;
    });
  }

  /* --- 3. run progress -------------------------------------------------- */

  function label(text, detail) {
    var fragment = document.createDocumentFragment();
    var main = document.createElement("span");
    main.className = "progress-label stage-name";
    main.textContent = text;
    fragment.append(main);
    if (detail) {
      var extra = document.createElement("span");
      extra.className = "progress-detail stage-detail";
      extra.textContent = detail;
      fragment.append(extra);
    }
    return fragment;
  }

  function followRun(list) {
    var working = document.getElementById("progress-working");
    var source = new EventSource("/runs/" + list.dataset.thread + "/events");
    var seen = new Set();

    source.onmessage = function (message) {
      var event = JSON.parse(message.data);

      if (event.stage === "done") {
        source.close();
        window.location.reload();
        return;
      }

      if (event.stage === "failed") {
        source.close();
        working.className = "progress-step progress-failed stage-row fail";
        working.innerHTML = "";
        working.append(label(event.label, event.detail));
        return;
      }

      var key = event.stage + ":" + event.count;
      if (seen.has(key)) return;
      seen.add(key);

      var step = document.createElement("li");
      step.className = "progress-step stage-row done";
      var tick = document.createElement("span");
      tick.className = "tick stage-ico";
      tick.setAttribute("aria-hidden", "true");
      tick.textContent = "✓";
      step.append(tick, label(event.label, event.detail));
      list.insertBefore(step, working);
      flash(step);
    };

    source.onerror = function () {
      source.close();
      window.location.reload();
    };
  }

  /* --- approve dialog --------------------------------------------------- */

  function openApprove() {
    var overlay = document.getElementById("approve-dialog");
    if (!overlay) return;
    lastFocus = document.activeElement;
    overlay.classList.add("open");
    overlay.setAttribute("aria-hidden", "false");
    var focusable = overlay.querySelector("[data-confirm-approve], button");
    if (focusable) focusable.focus();
  }

  function closeApprove() {
    var overlay = document.getElementById("approve-dialog");
    if (!overlay) return;
    overlay.classList.remove("open");
    overlay.setAttribute("aria-hidden", "true");
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
  }

  function confirmApprove() {
    var form = document.getElementById("approve-form");
    if (!form) return;
    var busy = form.getAttribute("data-busy");
    var btn = document.querySelector("[data-confirm-approve]");
    if (btn && busy) {
      btn.disabled = true;
      btn.textContent = busy;
    }
    form.submit();
  }

  /* --- wiring ----------------------------------------------------------- */

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;

    if (form.hasAttribute("hx-post")) {
      saveField(form, event);
      return;
    }

    var busy = form.getAttribute("data-busy");
    if (busy) {
      var button = form.querySelector("button[type=submit]");
      if (button) {
        button.disabled = true;
        button.textContent = busy;
      }
    }
  });

  document.addEventListener("click", function (event) {
    var link = event.target.closest(".cite[data-doc]");
    if (link) {
      openCitation(link, event);
      return;
    }

    if (event.target.closest("#theme-toggle")) {
      toggleTheme();
      return;
    }

    if (event.target.closest("[data-open-approve]")) {
      openApprove();
      return;
    }

    if (event.target.closest("[data-close-approve]")) {
      closeApprove();
      return;
    }

    if (event.target.closest("[data-confirm-approve]")) {
      confirmApprove();
      return;
    }

    var attn = event.target.closest("[data-attn-toggle]");
    if (attn) {
      var group = attn.closest(".attn-group");
      var open = group.classList.toggle("open");
      attn.setAttribute("aria-expanded", open ? "true" : "false");
      return;
    }

    var overlay = document.getElementById("approve-dialog");
    if (overlay && overlay.classList.contains("open") && event.target === overlay) {
      closeApprove();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeApprove();

    var overlay = document.getElementById("approve-dialog");
    if (!overlay || !overlay.classList.contains("open") || event.key !== "Tab")
      return;

    var focusables = overlay.querySelectorAll(
      "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"
    );
    if (!focusables.length) return;
    var first = focusables[0];
    var last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(currentTheme());
    tagCitationsWithThread();
    var list = document.getElementById("progress");
    if (list) followRun(list);
  });
})();
