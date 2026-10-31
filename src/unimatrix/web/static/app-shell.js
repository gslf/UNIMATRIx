"use strict";
// Shared application navigation. This template contains only static application markup.
(function () {
  const host = document.getElementById("app-top");
  if (!host) return;
  host.innerHTML = `
  <a class="brand" href="/#/" aria-label="UNIMATRIx Polis, home">
    <span class="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32" width="28" height="28"><defs><radialGradient id="bm" cx="50%" cy="40%" r="60%"><stop offset="0" stop-color="#ffe27a"/><stop offset="1" stop-color="#b59a00"/></radialGradient></defs><circle cx="16" cy="16" r="14" fill="none" stroke="url(#bm)" stroke-width="1.5" opacity=".7"/><circle cx="16" cy="9" r="3" fill="url(#bm)"/><circle cx="9" cy="20" r="3" fill="#4aa3df"/><circle cx="23" cy="20" r="3" fill="#8bc34a"/><path d="M16 9 9 20h14z" fill="none" stroke="#dedede" stroke-opacity=".35"/></svg>
    </span>
    <span class="brand-text">UNIMATRIx <span>POLIS</span></span>
  </a>

  <nav class="tabbar" id="tabbar" aria-label="Main">
    <ul>
      <li><a href="/#/" data-route="home"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1z"/></svg><span>Home</span></a></li>
      <li><a href="/#/leaderboard" data-route="leaderboard"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h16"/><path d="M6 20v-7h3v7M10.5 20V5h3v15M15 20v-10h3v10"/></svg><span>Leaderboard</span></a></li>
      <li><a href="/#/runs" data-route="runs"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h9M4 18h6"/><path d="M15.5 14.5v6l5-3z"/></svg><span>Runs</span><em class="tab-dot" id="tab-live-dot" hidden><span class="sr-only">(a run is live)</span></em></a></li>
      <li><a href="/#/models" data-route="models"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h10v10H7z"/><path d="M10 10h4v4h-4zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/></svg><span>Models</span></a></li>
      <li><a href="/recipe-lab" data-route="lab" aria-label="Society Lab" title="Society Lab: design your own village"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6M10 3v6l-5.5 9.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3"/><path d="M7.5 15h9"/></svg><span>Lab</span></a></li>
    </ul>
    <i class="tab-ink" id="tab-ink" aria-hidden="true"></i>
  </nav>

  <div class="top-actions">
    <a id="live-pill" class="live-pill" href="/#/runs" hidden><i aria-hidden="true"></i><span>LIVE</span><b id="live-pill-text"></b></a>
    <a class="icon-btn" href="/#/about" data-route="about" aria-label="About and help" title="About and help"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.4 2.33c-.55.22-.9.74-.9 1.33v.6M12 16.8v.4"/></svg></a>
    <a class="button primary cta-top" href="/#/play" data-route="play" aria-label="Start a run"><svg class="ic fill" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z"/></svg><span>Start a run</span></a>
  </div>
`;
  const nav = document.getElementById("tabbar");
  const ink = document.getElementById("tab-ink");
  function moveInk() {
    const active = nav.querySelector('a[aria-current="page"]');
    if (!active) { ink.style.setProperty("--ink-o", "0"); return; }
    const navRect = nav.getBoundingClientRect(), rect = active.getBoundingClientRect();
    const width = Math.min(rect.width * 0.6, 46);
    ink.style.setProperty("--ink-x", `${rect.left - navRect.left + (rect.width - width) / 2}px`);
    ink.style.setProperty("--ink-w", `${width}px`);
    ink.style.setProperty("--ink-o", "1");
  }
  if (host.dataset.page === "lab") {
    const lab = nav.querySelector('[data-route="lab"]');
    lab.setAttribute("aria-current", "page");
    lab.setAttribute("href", "#overview");
  }
  window.UnimatrixNav = { moveInk };
  window.addEventListener("resize", moveInk);
  if (typeof ResizeObserver === "function") new ResizeObserver(moveInk).observe(nav);
  moveInk();
})();
