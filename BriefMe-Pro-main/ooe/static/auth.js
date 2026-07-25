/* ============================================================
   BriefMe-Pro — Shared Auth Utilities
   Particle background, password strength, form helpers
   ============================================================ */

"use strict";

/* ── Particle star field ────────────────────────────────────── */
function initParticles(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let W, H, particles, animId;

  const PARTICLE_COUNT = 110;
  const SPEEDS = [0.08, 0.12, 0.18, 0.28];

  function resize() {
    W = canvas.width  = canvas.offsetWidth;
    H = canvas.height = canvas.offsetHeight;
  }

  function makeParticle() {
    return {
      x: Math.random() * W,
      y: Math.random() * H,
      r: Math.random() * 1.2 + 0.2,
      vx: (Math.random() - 0.5) * SPEEDS[Math.floor(Math.random() * SPEEDS.length)],
      vy: (Math.random() - 0.5) * SPEEDS[Math.floor(Math.random() * SPEEDS.length)],
      alpha: Math.random() * 0.6 + 0.1,
      pulse: Math.random() * Math.PI * 2,
      pulseSpeed: 0.008 + Math.random() * 0.012,
      color: Math.random() > 0.7 ? "103,233,255" : Math.random() > 0.5 ? "61,245,192" : "200,240,255",
    };
  }

  function init() {
    resize();
    particles = Array.from({ length: PARTICLE_COUNT }, makeParticle);
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    for (const p of particles) {
      p.pulse += p.pulseSpeed;
      const a = p.alpha * (0.6 + 0.4 * Math.sin(p.pulse));
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${p.color},${a.toFixed(2)})`;
      ctx.fill();

      p.x += p.vx;
      p.y += p.vy;
      if (p.x < -4) p.x = W + 4;
      if (p.x > W + 4) p.x = -4;
      if (p.y < -4) p.y = H + 4;
      if (p.y > H + 4) p.y = -4;
    }
    animId = requestAnimationFrame(draw);
  }

  window.addEventListener("resize", () => { resize(); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cancelAnimationFrame(animId);
    } else {
      animId = requestAnimationFrame(draw);
    }
  });

  init();
  draw();
}

/* ── Password strength scorer (0–4) ────────────────────────── */
function scorePassword(pw) {
  if (!pw) return 0;
  let score = 0;
  if (pw.length >= 8)  score++;
  if (pw.length >= 12) score++;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) score++;
  if (/[0-9]/.test(pw)) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  // clamp to 1-4 when non-empty
  return Math.min(4, Math.max(1, score));
}

const STRENGTH_LABELS = ["", "Weak", "Fair", "Strong", "Excellent"];
const STRENGTH_HINTS  = [
  "",
  "Use uppercase, numbers, or symbols to strengthen.",
  "Add a symbol or more length.",
  "Good — one more tweak makes it excellent.",
  "Strong password.",
];

/* ── Bind strength meter ────────────────────────────────────── */
function bindStrengthMeter(inputId, wrapId) {
  const input = document.getElementById(inputId);
  const wrap  = document.getElementById(wrapId);
  if (!input || !wrap) return;

  const label = wrap.querySelector(".strength-label");

  function update() {
    const level = scorePassword(input.value);
    wrap.dataset.level = input.value ? level : "";
    if (label) {
      label.textContent = input.value ? STRENGTH_LABELS[level] : "";
      label.title = input.value ? STRENGTH_HINTS[level] : "";
    }
  }

  input.addEventListener("input", update);
  update();
}

/* ── Show/hide password toggle ─────────────────────────────── */
const EYE_OPEN = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`;
const EYE_SHUT = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;

function bindShowHide(inputId, btnId) {
  const input = document.getElementById(inputId);
  const btn   = document.getElementById(btnId);
  if (!input || !btn) return;
  btn.innerHTML = EYE_OPEN;
  btn.setAttribute("aria-label", "Show password");
  btn.addEventListener("click", () => {
    const isText = input.type === "text";
    input.type = isText ? "password" : "text";
    btn.innerHTML = isText ? EYE_OPEN : EYE_SHUT;
    btn.setAttribute("aria-label", isText ? "Show password" : "Hide password");
  });
}

/* ── Confirm-match indicator ────────────────────────────────── */
function bindConfirmMatch(pwId, confirmId, indicatorId) {
  const pw      = document.getElementById(pwId);
  const confirm = document.getElementById(confirmId);
  const ind     = document.getElementById(indicatorId);
  if (!pw || !confirm || !ind) return;

  function check() {
    if (!confirm.value) {
      ind.className = "match-indicator";
      ind.textContent = "";
      return;
    }
    if (pw.value === confirm.value) {
      ind.className = "match-indicator ok";
      ind.textContent = "✓";
    } else {
      ind.className = "match-indicator error";
      ind.textContent = "✕";
    }
  }

  pw.addEventListener("input", check);
  confirm.addEventListener("input", check);
}

/* ── Debounced email availability check ─────────────────────── */
function bindEmailCheck(inputId, msgId) {
  const input = document.getElementById(inputId);
  const msg   = document.getElementById(msgId);
  if (!input || !msg) return;

  let timer;
  input.addEventListener("blur", () => {
    const email = input.value.trim();
    if (!email || !email.includes("@")) return;
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const res  = await fetch(`/api/auth/check-email?email=${encodeURIComponent(email)}`, { credentials: "same-origin" });
        const data = await res.json().catch(() => ({}));
        if (data.available === false) {
          msg.textContent = "This email is already registered.";
          msg.className   = "auth-field-msg visible error";
          input.closest(".auth-field")?.classList.add("is-error");
        } else {
          msg.className = "auth-field-msg";
        }
      } catch (_) { /* network — silently ignore */ }
    }, 380);
  });

  input.addEventListener("input", () => {
    msg.className = "auth-field-msg";
    input.closest(".auth-field")?.classList.remove("is-error");
  });
}

/* ── Countdown timer helper ─────────────────────────────────── */
function startCountdown(spanId, seconds, onDone) {
  const el = document.getElementById(spanId);
  if (!el) return;
  let remaining = seconds;
  el.textContent = remaining;
  const iv = setInterval(() => {
    remaining--;
    el.textContent = remaining;
    if (remaining <= 0) {
      clearInterval(iv);
      if (onDone) onDone();
    }
  }, 1000);
  return iv;
}

/* ── Show banner ────────────────────────────────────────────── */
function showBanner(bannerId, type, html) {
  const el = document.getElementById(bannerId);
  if (!el) return;
  const icons = {
    error:   `<span class="auth-banner-icon">⚠</span>`,
    success: `<span class="auth-banner-icon">✓</span>`,
    info:    `<span class="auth-banner-icon">ℹ</span>`,
  };
  el.className = `auth-banner ${type} visible`;
  el.innerHTML = (icons[type] || "") + `<span>${html}</span>`;
}

function hideBanner(bannerId) {
  const el = document.getElementById(bannerId);
  if (el) el.className = "auth-banner";
}

/* ── Submit button helpers ─────────────────────────────────── */
function setBtnLoading(btn, text) {
  btn.disabled = true;
  btn.classList.add("loading");
  btn.innerHTML = `<span class="spinner"></span>${text}`;
}

function setBtnReady(btn, text) {
  btn.disabled = false;
  btn.classList.remove("loading");
  btn.textContent = text;
}

/* ── Unified form submit helper ─────────────────────────────── */
async function submitForm({ url, data, method = "POST", btnEl, btnLoadText, btnReadyText, bannerId, onSuccess, on429 }) {
  hideBanner(bannerId);
  setBtnLoading(btnEl, btnLoadText || "Processing…");

  try {
    const res  = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
      credentials: "same-origin",
    });
    const body = await res.json().catch(() => ({}));

    if (res.ok) {
      if (onSuccess) onSuccess(body);
    } else if (res.status === 429) {
      const retryAfter = parseInt(res.headers?.get?.("Retry-After") || "60", 10);
      if (on429) {
        on429(body, retryAfter);
      } else {
        showBanner(bannerId, "error",
          `Too many attempts. Try again in <strong><span class="auth-countdown" id="retryCountdown">${retryAfter}</span>s</strong>.`
        );
        startCountdown("retryCountdown", retryAfter, () => {
          hideBanner(bannerId);
          setBtnReady(btnEl, btnReadyText || "Try again");
        });
        return; // don't re-enable btn during countdown
      }
    } else {
      showBanner(bannerId, "error", body.error || "Something went wrong. Please try again.");
      setBtnReady(btnEl, btnReadyText || "Try again");
    }
  } catch (_) {
    showBanner(bannerId, "error", "Network error — please check your connection.");
    setBtnReady(btnEl, btnReadyText || "Try again");
  }
}

/* ── Ticker animation (left panel stats) ───────────────────── */
function animateTicker(elId, target, duration = 1200) {
  const el = document.getElementById(elId);
  if (!el) return;
  const start = performance.now();
  const from  = 0;
  function step(now) {
    const t = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = Math.round(from + (target - from) * eased).toLocaleString();
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

/* ── Fetch live stats for left panel ────────────────────────── */
async function loadAuthStats() {
  try {
    const res  = await fetch("/api/metrics", { credentials: "same-origin" });
    const data = await res.json().catch(() => ({}));
    if (data.total_events !== undefined) animateTicker("statEvents",  data.total_events);
    if (data.sources)                    animateTicker("statSources", data.sources.length);
  } catch (_) { /* non-fatal */ }
}
