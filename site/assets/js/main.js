/* ═══════════════════════════════════════════════════════════════════════════
   Easy Server Cloner — Website-Interaktionen

   Alles hier ist Beiwerk: die Seite funktioniert vollständig ohne JavaScript.
   Kein Framework, keine Abhängigkeiten — läuft als statische Datei auf Vercel.
   ═══════════════════════════════════════════════════════════════════════ */

(() => {
  "use strict";

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  /* ── Navigation ──────────────────────────────────────────────────────── */

  function initNav() {
    const nav = $(".nav");
    const toggle = $(".nav-toggle");
    const links = $(".nav-links");
    if (!nav) return;

    const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });

    if (!toggle || !links) return;

    const close = () => {
      links.classList.remove("open");
      toggle.setAttribute("aria-expanded", "false");
    };

    toggle.addEventListener("click", () => {
      const open = links.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });

    links.addEventListener("click", (e) => {
      if (e.target.closest("a")) close();
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") close();
    });

    // Wird das Fenster breit genug, verschwindet das Menü ohnehin per CSS.
    window.addEventListener("resize", () => {
      if (window.innerWidth > 860) close();
    });
  }

  /* ── Slideshow ───────────────────────────────────────────────────────── */

  function initSlideshow() {
    const root = $("[data-slideshow]");
    if (!root) return;

    const slides = $$(".slide", root);
    const tabs = $$(".slide-tab", root);
    const dots = $$(".slide-dot", root);
    const prev = $(".slide-prev", root);
    const next = $(".slide-next", root);
    const counter = $(".slide-counter", root);
    if (!slides.length) return;

    let index = 0;
    let timer = null;
    // Nach einem Klick soll die Show nicht weiterspringen — der Nutzer führt.
    let userTookOver = false;

    function show(i, { fromUser = false } = {}) {
      index = (i + slides.length) % slides.length;

      slides.forEach((s, n) => s.classList.toggle("active", n === index));
      tabs.forEach((t, n) => t.setAttribute("aria-selected", String(n === index)));
      dots.forEach((d, n) => d.setAttribute("aria-current", String(n === index)));

      if (prev) prev.disabled = index === 0;
      if (next) next.disabled = index === slides.length - 1;
      if (counter) counter.textContent = `${String(index + 1).padStart(2, "0")} / ${String(slides.length).padStart(2, "0")}`;

      if (fromUser) stopAuto();
      restartVisualAnimations();
    }

    // Die Illustrationen sind SMIL-Animationen; ohne Neustart würde eine
    // erneut geöffnete Folie eingefroren wirken.
    function restartVisualAnimations() {
      if (reduceMotion) return;
      const svg = $(".slide.active .slide-visual svg", root);
      if (svg && typeof svg.setCurrentTime === "function") {
        try { svg.setCurrentTime(0); } catch { /* ältere Browser: egal */ }
      }
    }

    function stopAuto() {
      userTookOver = true;
      if (timer) { clearInterval(timer); timer = null; }
    }

    function startAuto() {
      if (reduceMotion || userTookOver || timer) return;
      timer = setInterval(() => {
        if (document.hidden) return;
        show(index + 1);
      }, 7000);
    }

    tabs.forEach((tab, n) => tab.addEventListener("click", () => show(n, { fromUser: true })));
    dots.forEach((dot, n) => dot.addEventListener("click", () => show(n, { fromUser: true })));
    prev?.addEventListener("click", () => show(index - 1, { fromUser: true }));
    next?.addEventListener("click", () => show(index + 1, { fromUser: true }));

    // Pfeiltasten, sobald die Tab-Leiste den Fokus hat
    root.addEventListener("keydown", (e) => {
      if (!e.target.closest(".slide-tabs")) return;
      if (e.key === "ArrowRight") { e.preventDefault(); show(index + 1, { fromUser: true }); tabs[index]?.focus(); }
      if (e.key === "ArrowLeft") { e.preventDefault(); show(index - 1, { fromUser: true }); tabs[index]?.focus(); }
    });

    // Wischen auf dem Handy
    let touchX = null;
    root.addEventListener("touchstart", (e) => { touchX = e.changedTouches[0].clientX; }, { passive: true });
    root.addEventListener("touchend", (e) => {
      if (touchX === null) return;
      const delta = e.changedTouches[0].clientX - touchX;
      if (Math.abs(delta) > 55) show(index + (delta < 0 ? 1 : -1), { fromUser: true });
      touchX = null;
    }, { passive: true });

    show(0);

    // Erst loslaufen, wenn die Show tatsächlich zu sehen ist.
    if ("IntersectionObserver" in window) {
      new IntersectionObserver((entries) => {
        entries.forEach((entry) => (entry.isIntersecting ? startAuto() : timer && stopAutoQuietly()));
      }, { threshold: 0.35 }).observe(root);
    } else {
      startAuto();
    }

    function stopAutoQuietly() {
      if (timer) { clearInterval(timer); timer = null; }
    }
  }

  /* ── Kopieren ────────────────────────────────────────────────────────── */

  function initCopy() {
    $$(".code").forEach((block) => {
      const pre = $("pre", block);
      if (!pre) return;

      const btn = document.createElement("button");
      btn.className = "copy";
      btn.type = "button";
      btn.textContent = "Copy";
      btn.setAttribute("aria-label", "Copy code to clipboard");

      btn.addEventListener("click", async () => {
        // Kommentarzeilen und der Prompt gehören nicht in die Zwischenablage.
        const text = [...pre.childNodes]
          .filter((n) => !(n.nodeType === 1 && n.classList.contains("c")))
          .map((n) => n.textContent)
          .join("")
          .split("\n")
          .map((line) => line.replace(/^\s*\$\s?/, ""))
          .join("\n")
          .trim();

        try {
          await navigator.clipboard.writeText(text);
          btn.textContent = "Copied";
        } catch {
          btn.textContent = "Press Ctrl+C";
          const range = document.createRange();
          range.selectNodeContents(pre);
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
        }
        btn.classList.add("done");
        setTimeout(() => {
          btn.textContent = "Copy";
          btn.classList.remove("done");
        }, 1800);
      });

      block.appendChild(btn);
    });
  }

  /* ── Auftauchen beim Scrollen ────────────────────────────────────────── */

  function initReveal() {
    const items = $$(".reveal");
    if (!items.length) return;

    if (reduceMotion || !("IntersectionObserver" in window)) {
      items.forEach((el) => el.classList.add("in"));
      return;
    }

    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        // Kleiner Versatz je Element, damit Gruppen gestaffelt erscheinen.
        const delay = Number(entry.target.dataset.delay || 0);
        setTimeout(() => entry.target.classList.add("in"), delay);
        io.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -12% 0px", threshold: 0.08 });

    items.forEach((el) => io.observe(el));
  }

  /* ── Lichtschein auf Karten ──────────────────────────────────────────── */

  function initCardGlow() {
    if (reduceMotion || !window.matchMedia("(hover: hover)").matches) return;

    $$(".card").forEach((card) => {
      card.addEventListener("pointermove", (e) => {
        const r = card.getBoundingClientRect();
        card.style.setProperty("--mx", `${e.clientX - r.left}px`);
        card.style.setProperty("--my", `${e.clientY - r.top}px`);
      });
    });
  }

  /* ── Akkordeon (FAQ) ─────────────────────────────────────────────────── */

  function initAccordion() {
    $$(".faq-item").forEach((item) => {
      const btn = $(".faq-q", item);
      const panel = $(".faq-a", item);
      if (!btn || !panel) return;

      btn.addEventListener("click", () => {
        const open = item.classList.toggle("open");
        btn.setAttribute("aria-expanded", String(open));
        panel.hidden = false;
      });
    });

    // Direktlink auf eine einzelne Frage: /faq.html#frage-id
    // Ohne Hash liefert `location.hash` einen leeren String — der ist zwar
    // falsy, aber nicht nullish, würde `?.` also passieren. Deshalb explizit
    // prüfen. Ein von Hand eingetippter Hash kann zudem ein ungültiger
    // Selektor sein, was querySelector werfen lässt.
    if (!location.hash) return;

    let target = null;
    try {
      target = document.querySelector(location.hash);
    } catch {
      return;
    }

    if (target && target.classList.contains("faq-item")) {
      target.classList.add("open");
      $(".faq-q", target)?.setAttribute("aria-expanded", "true");
    }
  }

  /* ── Inhaltsverzeichnis mitlaufen lassen ─────────────────────────────── */

  function initTocSpy() {
    const links = $$(".toc a");
    if (!links.length || !("IntersectionObserver" in window)) return;

    const sections = links
      .map((a) => document.querySelector(a.getAttribute("href")))
      .filter(Boolean);
    if (!sections.length) return;

    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        links.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#${entry.target.id}`));
      });
    }, { rootMargin: "-25% 0px -65% 0px" });

    sections.forEach((s) => io.observe(s));
  }

  /* ── Pixel-Feld im Hintergrund ───────────────────────────────────────── */

  function initPixelField() {
    const canvas = $("#pixelfield");
    if (!canvas || reduceMotion) return;

    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    let pixels = [];
    let width = 0;
    let height = 0;
    let raf = null;

    function resize() {
      // Auf Retina-Displays doppelt so viele Gerätepixel, aber gedeckelt:
      // mehr als 2× bringt optisch nichts und kostet spürbar Leistung.
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const density = Math.min(70, Math.floor((width * height) / 24000));
      pixels = Array.from({ length: density }, spawn);
    }

    function spawn() {
      const size = 2 + Math.random() * 4;
      return {
        x: Math.random() * width,
        y: Math.random() * height,
        size,
        // Größere Pixel driften schneller — das erzeugt Tiefe.
        vx: (0.08 + Math.random() * 0.28) * (size / 4),
        vy: (Math.random() - 0.5) * 0.12,
        alpha: 0.08 + Math.random() * 0.3,
        phase: Math.random() * Math.PI * 2,
      };
    }

    function frame(now) {
      ctx.clearRect(0, 0, width, height);

      for (const p of pixels) {
        p.x += p.vx;
        p.y += p.vy;

        // Am rechten Rand hinaus, links wieder herein — wie im Logo.
        if (p.x > width + 10) {
          p.x = -10;
          p.y = Math.random() * height;
        }
        if (p.y < -10) p.y = height + 10;
        if (p.y > height + 10) p.y = -10;

        const twinkle = 0.72 + 0.28 * Math.sin(now / 900 + p.phase);
        ctx.fillStyle = `rgba(155, 138, 255, ${(p.alpha * twinkle).toFixed(3)})`;
        ctx.fillRect(p.x, p.y, p.size, p.size);
      }

      raf = requestAnimationFrame(frame);
    }

    function start() {
      if (raf === null) raf = requestAnimationFrame(frame);
    }
    function stop() {
      if (raf !== null) { cancelAnimationFrame(raf); raf = null; }
    }

    resize();
    start();

    let resizeTimer;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(resize, 180);
    });

    // Im Hintergrund-Tab nicht weiterrechnen.
    document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
  }

  /* ── Jahr im Footer ──────────────────────────────────────────────────── */

  function initYear() {
    $$("[data-year]").forEach((el) => (el.textContent = String(new Date().getFullYear())));
  }

  /* ── Start ───────────────────────────────────────────────────────────── */

  function init() {
    initNav();
    initSlideshow();
    initCopy();
    initReveal();
    initCardGlow();
    initAccordion();
    initTocSpy();
    initPixelField();
    initYear();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
