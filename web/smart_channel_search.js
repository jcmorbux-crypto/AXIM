// Smart Channel Search - one reusable search-as-you-type channel/
// provider picker, shared across every page that needs to select a
// Telegram channel (Signal Inspection, Parsing Rules, Provider
// onboarding, historical import, backtesting, Fund creation, provider
// assignment) instead of each page building its own static <select>
// or its own inconsistent search box. Backed by GET /api/channels/
// search (core/database.py's search_channels - ranked, status-aware,
// tolerant of partial words and minor typos via subsequence matching),
// never relies on whatever channels happened to be loaded when the
// page opened.
//
// Usage: SmartChannelSearch.mount("container-id", {
//   placeholder: "Search for a signal provider...",
//   recentKey: "inspector-signals",   // localStorage bucket for "recently used" - pick a
//                                      // distinct key per integration point so unrelated
//                                      // pickers don't share each other's recent list
//   onSelect: (channel, suggestedAction) => { ... },
// }) -> returns { setSelected(channel), getSelected(), clear() }
//
// channel.status is "connected" (added AND has a real parsing profile/
// recommendation on file), "needs_setup" (added, never analyzed), or
// "available" (visible via Telegram sync, not yet added) - suggestedAction
// is a human-readable label matching the Smart Channel Workflow spec
// ("Open parsing profile" / "Analyze Channel" / "Add and Analyze"); the
// component surfaces this so a caller CAN branch on it, but doesn't
// force navigation itself - what "selecting a channel" actually does
// is legitimately different per page (Signal Inspection opens rules;
// a backtest filter just fills a value).
const SmartChannelSearch = (() => {
  const RECENT_KEY_PREFIX = "axim-recent-channels-";
  const DEBOUNCE_MS = 200;
  const MAX_RECENT = 5;

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s ?? "";
    return d.innerHTML;
  }

  function getRecent(recentKey) {
    try {
      return JSON.parse(localStorage.getItem(RECENT_KEY_PREFIX + recentKey) || "[]");
    } catch (e) {
      return [];
    }
  }

  function pushRecent(recentKey, channel) {
    let recent = getRecent(recentKey).filter(c => c.id !== channel.id);
    recent.unshift({
      id: channel.id, title: channel.title, username: channel.username,
      status: channel.status, last_signal_at: channel.last_signal_at,
    });
    recent = recent.slice(0, MAX_RECENT);
    try { localStorage.setItem(RECENT_KEY_PREFIX + recentKey, JSON.stringify(recent)); } catch (e) { /* storage full/disabled - not critical */ }
  }

  const STATUS_META = {
    connected: { text: "Connected", cls: "on", action: "Open parsing profile" },
    needs_setup: { text: "Needs Setup", cls: "warn", action: "Analyze Channel" },
    available: { text: "Available", cls: "off", action: "Add and Analyze" },
  };

  function statusMeta(status) {
    return STATUS_META[status] || STATUS_META.available;
  }

  function mount(containerId, options) {
    const container = document.getElementById(containerId);
    if (!container) return null;
    const opts = Object.assign({
      placeholder: "Search for a signal provider...",
      recentKey: "default",
      includeHistoricalSources: false,
      onSelect: () => {},
    }, options);

    container.classList.add("smart-channel-search");
    container.innerHTML = `
      <input type="text" class="scs-input" placeholder="${escapeHtml(opts.placeholder)}" autocomplete="off" aria-label="${escapeHtml(opts.placeholder)}">
      <div class="scs-selected" style="display:none;"></div>
      <div class="scs-results" style="display:none;"></div>
    `;
    const input = container.querySelector(".scs-input");
    const resultsEl = container.querySelector(".scs-results");
    const selectedEl = container.querySelector(".scs-selected");

    let results = [];
    let activeIndex = -1;
    let debounceTimer = null;
    let selectedChannel = null;
    let requestId = 0;
    // Results default to the OPT SIGNALS folder (database.search_channels'
    // default_folder_only) - real personal contacts/unrelated Telegram
    // chats stay hidden until the operator explicitly widens, confirmed
    // live this was otherwise ~150 irrelevant results mixed with real
    // providers.
    let allSources = false;

    function itemHtml(c, i) {
      const meta = statusMeta(c.status);
      const lastSignal = c.last_signal_at ? `&middot; last signal ${new Date(c.last_signal_at).toLocaleDateString()}` : "";
      return `
        <div class="scs-item" data-index="${i}" role="option">
          <div class="scs-item-main">
            <div class="scs-item-title">${escapeHtml(c.title || "(no title)")}</div>
            <div class="scs-item-meta">${c.username ? "@" + escapeHtml(c.username) : ""} ${lastSignal}</div>
          </div>
          <span class="badge ${meta.cls}">${meta.text}</span>
        </div>
      `;
    }

    function widenLinkHtml() {
      if (allSources) return "";
      return `<div class="scs-widen"><button type="button" class="scs-widen-link">Not finding it? Search all your Telegram chats &rarr;</button></div>`;
    }

    function renderEmpty(query) {
      resultsEl.innerHTML = `<div class="scs-empty">${query ? "No matching channels found." : "Start typing to search connected Telegram sources."}</div>${widenLinkHtml()}`;
      resultsEl.style.display = "block";
      bindWidenLink();
    }

    function renderResults(list, sectionLabel) {
      results = list;
      activeIndex = -1;
      if (!list.length) {
        renderEmpty(input.value.trim());
        return;
      }
      resultsEl.innerHTML = (sectionLabel ? `<div class="scs-section-label">${escapeHtml(sectionLabel)}</div>` : "")
        + list.map((c, i) => itemHtml(c, i)).join("") + widenLinkHtml();
      resultsEl.style.display = "block";
      bindWidenLink();
    }

    function bindWidenLink() {
      const link = resultsEl.querySelector(".scs-widen-link");
      if (!link) return;
      link.addEventListener("click", () => {
        allSources = true;
        const query = input.value.trim();
        if (query) runSearch(query); else showRecentOrDefault();
      });
    }

    async function runSearch(query) {
      const thisRequest = ++requestId;
      try {
        const hist = opts.includeHistoricalSources ? "&include_historical_sources=true" : "";
        const wide = allSources ? "&all_sources=true" : "";
        const res = await fetch(`/api/channels/search?q=${encodeURIComponent(query)}&limit=20${hist}${wide}`, { credentials: "same-origin" });
        const data = res.ok ? await res.json() : [];
        if (thisRequest !== requestId) return; // a newer keystroke's request already landed
        renderResults(data, null);
      } catch (e) {
        if (thisRequest !== requestId) return;
        renderResults([], null);
      }
    }

    function showRecentOrDefault() {
      const recent = getRecent(opts.recentKey);
      if (recent.length) {
        renderResults(recent, "Recently used");
      } else {
        runSearch("");
      }
    }

    function selectChannel(channel) {
      selectedChannel = channel;
      pushRecent(opts.recentKey, channel);
      input.value = "";
      input.style.display = "none";
      resultsEl.style.display = "none";
      const meta = statusMeta(channel.status);
      selectedEl.style.display = "flex";
      selectedEl.innerHTML = `
        <div class="scs-item-main">
          <div class="scs-item-title">${escapeHtml(channel.title || "(no title)")}</div>
          <div class="scs-item-meta">${channel.username ? "@" + escapeHtml(channel.username) : ""}</div>
        </div>
        <span class="badge ${meta.cls}">${meta.text}</span>
        <button type="button" class="subtle scs-change">Change</button>
      `;
      selectedEl.querySelector(".scs-change").addEventListener("click", () => {
        selectedChannel = null;
        selectedEl.style.display = "none";
        input.style.display = "block";
        input.value = "";
        input.focus();
      });
      opts.onSelect(channel, meta.action);
    }

    function updateActiveHighlight() {
      resultsEl.querySelectorAll(".scs-item").forEach((el, i) => {
        el.classList.toggle("active", i === activeIndex);
        if (i === activeIndex) el.scrollIntoView({ block: "nearest" });
      });
    }

    input.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      const query = input.value.trim();
      if (!query) { showRecentOrDefault(); return; }
      debounceTimer = setTimeout(() => runSearch(query), DEBOUNCE_MS);
    });
    input.addEventListener("focus", () => {
      if (!input.value.trim()) showRecentOrDefault();
      else resultsEl.style.display = "block";
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (resultsEl.style.display === "none") { showRecentOrDefault(); return; }
        activeIndex = Math.min(activeIndex + 1, results.length - 1);
        updateActiveHighlight();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        updateActiveHighlight();
      } else if (e.key === "Enter") {
        e.preventDefault();
        const pick = results[activeIndex >= 0 ? activeIndex : 0];
        if (pick) selectChannel(pick);
      } else if (e.key === "Escape") {
        resultsEl.style.display = "none";
        input.blur();
      }
    });

    resultsEl.addEventListener("click", (e) => {
      const item = e.target.closest(".scs-item");
      if (!item) return;
      const idx = parseInt(item.dataset.index, 10);
      if (results[idx]) selectChannel(results[idx]);
    });

    document.addEventListener("click", (e) => {
      if (!container.contains(e.target)) resultsEl.style.display = "none";
    });

    return {
      setSelected(channel) { if (channel) selectChannel(channel); },
      getSelected() { return selectedChannel; },
      clear() {
        selectedChannel = null;
        selectedEl.style.display = "none";
        input.style.display = "block";
        input.value = "";
        resultsEl.style.display = "none";
      },
    };
  }

  return { mount };
})();
