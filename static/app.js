(() => {
  const COLORS = ["#0F6E56", "#C84B31", "#2B5B84", "#8C5E2A", "#4A5568", "#7C3F58"];
  const MODES = [
    { val: "transit", short: "地铁公交" },
    { val: "driving", short: "开车打车" },
    { val: "cycling", short: "骑行" },
    { val: "walking", short: "步行" },
  ];

  const CITY_CENTERS = {
    北京: [39.94, 116.40],
    上海: [31.22, 121.46],
    深圳: [22.543, 114.058],
    广州: [23.129, 113.264],
    杭州: [30.274, 120.155],
    成都: [30.573, 104.067],
    南京: [32.06, 118.797],
    武汉: [30.593, 114.306],
    重庆: [29.563, 106.552],
    苏州: [31.299, 120.585],
    天津: [39.084, 117.201],
    西安: [34.342, 108.94],
    长沙: [28.228, 112.939],
    香港: [22.319, 114.169],
    "San Francisco": [37.775, -122.419],
  };

  const PRESETS = {
    bj_station: {
      city: "北京",
      category: "station",
      meetupTime: "18:30",
      fairness: "equal_time",
      sharedPrompt: "找个换乘方便的地铁站口直接汇合",
      participants: [
        {
          id: "p1",
          name: "我 (A)",
          address: "北京大学",
          resolved_address: "北京大学（北京市海淀区颐和园路5号）",
          coords: { lat: 39.9919, lng: 116.3059 },
          transport_mode: "transit",
          preference_note: "4号线或10号线出发",
        },
        {
          id: "p2",
          name: "朋友 (B)",
          address: "望京SOHO",
          resolved_address: "望京SOHO（北京市朝阳区望京街10号）",
          coords: { lat: 39.9968, lng: 116.4808 },
          transport_mode: "transit",
          preference_note: "14号线或15号线出发",
        },
      ],
    },
    sf_duo: {
      city: "San Francisco",
      category: "landmark",
      meetupTime: "18:30",
      fairness: "fair",
      sharedPrompt: "Easy landmark or plaza near transit and garage",
      participants: [
        {
          id: "p1",
          name: "Alex",
          address: "Mission District",
          resolved_address: "Mission District (San Francisco, CA)",
          coords: { lat: 37.7599, lng: -122.4148 },
          transport_mode: "transit",
          preference_note: "BART or Muni",
        },
        {
          id: "p2",
          name: "Sam",
          address: "Marina District",
          resolved_address: "Marina District (San Francisco, CA)",
          coords: { lat: 37.8015, lng: -122.4368 },
          transport_mode: "driving",
          preference_note: "Driving, needs parking",
        },
      ],
    },
  };

  const STORAGE_KEY = "lets_meet_runtime_keys_v2";

  const state = {
    city: "北京",
    participants: structuredClone(PRESETS.bj_station.participants),
    venueCategory: "station",
    fairnessMode: "equal_time",
    activeTab: "setup",
    snapMode: "half",
    tileMode: "osm",
    dirty: false,
    roomCode: null,
    votes: {},
    lastResponse: null,
    selectedRankIndex: 0,
    activeNavVenue: null,
    activeNavLegIndex: 0,
    publicHttpsUrl: "",
    lanUrl: "",
    isLocalAdmin: false,
    deferredInstallPrompt: null,
    // Interactive place picker & map tap-to-pick state
    suggestionsByIndex: {},
    pickState: null, // { idx, name, subtext, coords: {lat, lng} }
  };

  const map = L.map("map", { zoomControl: false }).setView([39.96, 116.40], 11);
  const osmTiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 });
  const esriTiles = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19 }
  );
  osmTiles.addTo(map);
  const mapLayerGroup = L.layerGroup().addTo(map);
  const routeLayerGroup = L.layerGroup().addTo(map);
  const originDraftLayer = L.layerGroup().addTo(map);
  const previewLayerGroup = L.layerGroup().addTo(map);

  const sheetEl = document.getElementById("appSheet");
  const dragZone = document.getElementById("sheetDragZone");

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function fetchJson(url, options = {}) {
    const resp = await fetch(url, options);
    const raw = await resp.text();
    let data = null;
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch {
        const looksHtml = /^\s*</.test(raw);
        throw new Error(
          looksHtml
            ? `公网通道返回了网页而非接口数据（HTTP ${resp.status}）。请刷新重试或改用同一 Wi‑Fi 局域网链接。`
            : `接口返回无法解析（HTTP ${resp.status}）`
        );
      }
    }
    if (!resp.ok) {
      throw new Error((data && (data.error || data.detail)) || `请求失败（HTTP ${resp.status}）`);
    }
    return data;
  }

  function showWarning(message) {
    const warn = document.getElementById("warningBox");
    warn.hidden = !message;
    warn.textContent = message || "";
  }

  function getActiveRuntimeKeys() {
    const rawKeys = loadRuntimeKeys();
    const cleaned = Object.fromEntries(
      Object.entries(rawKeys).filter(([_, v]) => v !== "" && v !== null && v !== undefined)
    );
    return Object.keys(cleaned).length > 0 ? cleaned : null;
  }

  // Sheet Snap & Drag
  function setSheetSnap(mode) {
    state.snapMode = mode;
    sheetEl.classList.remove("snap-peek", "snap-half", "snap-full");
    sheetEl.classList.add(`snap-${mode}`);
    setTimeout(() => fitMapToCurrentBounds(), 280);
  }

  let dragStartY = 0;
  let dragStartH = 0;
  let movedPx = 0;

  dragZone.addEventListener(
    "touchstart",
    (e) => {
      dragStartY = e.touches[0].clientY;
      dragStartH = sheetEl.getBoundingClientRect().height;
      movedPx = 0;
      sheetEl.style.transition = "none";
    },
    { passive: true }
  );

  dragZone.addEventListener(
    "touchmove",
    (e) => {
      const dy = dragStartY - e.touches[0].clientY;
      movedPx = Math.abs(dy);
      const nextH = Math.max(150, Math.min(window.innerHeight * 0.9, dragStartH + dy));
      sheetEl.style.height = `${nextH}px`;
    },
    { passive: true }
  );

  dragZone.addEventListener("touchend", () => {
    sheetEl.style.transition = "";
    sheetEl.style.height = "";
    if (movedPx < 8) {
      const next = state.snapMode === "half" ? "full" : state.snapMode === "full" ? "peek" : "half";
      setSheetSnap(next);
      return;
    }
    const curRatio = sheetEl.getBoundingClientRect().height / window.innerHeight;
    if (curRatio < 0.34) setSheetSnap("peek");
    else if (curRatio > 0.72) setSheetSnap("full");
    else setSheetSnap("half");
  });

  dragZone.addEventListener("click", () => {
    if ("ontouchstart" in window) return;
    const next = state.snapMode === "half" ? "full" : state.snapMode === "full" ? "peek" : "half";
    setSheetSnap(next);
  });

  function switchTab(tabName) {
    state.activeTab = tabName;
    document.querySelectorAll(".tab-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === tabName);
    });
    document.getElementById("tabPaneSetup").classList.toggle("active", tabName === "setup");
    document.getElementById("tabPaneResults").classList.toggle("active", tabName === "results");
    document.getElementById("tabPaneProfile").classList.toggle("active", tabName === "profile");
    document.getElementById("sheetScrollBody").scrollTop = 0;
    if (state.snapMode === "peek") setSheetSnap("half");
    if (tabName === "profile") refreshAccessAndHealthInfo();
  }

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  function markDirty(isDirty) {
    state.dirty = isDirty;
    const cta = document.getElementById("btnCompute");
    if (isDirty) {
      cta.textContent = "重新计算折中点";
      cta.classList.add("dirty");
    } else {
      cta.textContent = "找折中地点";
      cta.classList.remove("dirty");
    }
  }

  function getBiasCoords(excludeIdx = -1) {
    for (let i = 0; i < state.participants.length; i++) {
      if (i !== excludeIdx && state.participants[i]?.coords) {
        return state.participants[i].coords;
      }
    }
    if (state.city && CITY_CENTERS[state.city]) {
      const [lat, lng] = CITY_CENTERS[state.city];
      return { lat, lng };
    }
    const c = map.getCenter();
    return { lat: Number(c.lat.toFixed(5)), lng: Number(c.lng.toFixed(5)) };
  }

  function renderConfirmedOriginMarkers() {
    originDraftLayer.clearLayers();
    if (state.lastResponse && !state.dirty) return;
    state.participants.forEach((p, idx) => {
      if (!p.coords) return;
      const color = COLORS[idx % COLORS.length];
      const icon = L.divIcon({
        className: "p-pin",
        html: `<div style="background:${color};color:#fff;padding:3px 9px;border-radius:6px;font-weight:700;font-size:12px;white-space:nowrap;border:1.5px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.22);">${escapeHtml(
          p.name
        )}</div>`,
        iconAnchor: [28, 14],
      });
      L.marker([p.coords.lat, p.coords.lng], { icon })
        .bindPopup(`<strong>${escapeHtml(p.name)}</strong><br/>${escapeHtml(p.resolved_address || p.address)}`)
        .addTo(originDraftLayer);
    });
  }

  function updateParticipantStatusBar(idx) {
    const bar = document.getElementById(`resolvedBar-${idx}`);
    if (!bar) return;
    const p = state.participants[idx];
    if (p.coords) {
      const label = p.resolved_address || `${p.address} (${p.coords.lat.toFixed(4)}, ${p.coords.lng.toFixed(4)})`;
      bar.hidden = false;
      bar.className = "mp-resolved-bar locked";
      bar.innerHTML = `
        <span>✓ 已锁定：${escapeHtml(label)}</span>
        <button type="button" class="mp-repick-link" data-repick="${idx}">重选</button>
      `;
      bar.querySelector("[data-repick]")?.addEventListener("click", () => {
        searchParticipantPlaces(idx, true);
      });
    } else if (p.address && p.address.trim().length > 0) {
      bar.hidden = false;
      bar.className = "mp-resolved-bar unconfirmed";
      bar.innerHTML = `
        <span>待确认具体位置 · 点「搜地点」选准后点确定，避免同名跑偏</span>
        <button type="button" class="mp-repick-link" data-repick="${idx}">搜地点</button>
      `;
      bar.querySelector("[data-repick]")?.addEventListener("click", () => {
        searchParticipantPlaces(idx, true);
      });
    } else {
      bar.hidden = true;
      bar.innerHTML = "";
    }
  }

  // Preview & Confirm Flow (Candidate List + Map Tap-to-Pick)
  function showPreviewOnMap(idx, name, subtext, coords, collapseSheet = false) {
    state.pickState = { idx, name, subtext, coords };
    previewLayerGroup.clearLayers();
    const pName = state.participants[idx]?.name || `起点 ${idx + 1}`;
    const color = COLORS[idx % COLORS.length];

    const icon = L.divIcon({
      className: "pick-pin",
      html: `<div style="background:${color};color:#fff;padding:4px 10px;border-radius:999px;font-weight:700;font-size:12px;white-space:nowrap;border:2px solid #fff;box-shadow:0 4px 12px rgba(0,0,0,0.32);">📍 ${escapeHtml(
        name
      )}</div>`,
      iconAnchor: [40, 16],
    });
    L.marker([coords.lat, coords.lng], { icon }).addTo(previewLayerGroup);

    if (collapseSheet && state.snapMode !== "peek") {
      setSheetSnap("peek");
    }

    const banner = document.getElementById("mapPickBanner");
    document.getElementById("mapPickTarget").textContent = `正在为「${pName}」选起点 · 点击地图任意处可微调位置`;
    document.getElementById("mapPickTitle").textContent = name || "地图选点";
    document.getElementById("mapPickSub").textContent = subtext || `${coords.lat.toFixed(5)}, ${coords.lng.toFixed(5)}`;
    banner.hidden = false;

    const sheetH = collapseSheet ? 170 : sheetEl.getBoundingClientRect().height;
    const targetPoint = map.project([coords.lat, coords.lng], Math.max(map.getZoom(), 14));
    const offsetPoint = L.point(targetPoint.x, targetPoint.y + sheetH * 0.25);
    const offsetLatLng = map.unproject(offsetPoint, Math.max(map.getZoom(), 14));
    map.setView(offsetLatLng, Math.max(map.getZoom(), 14), { animate: true });
  }

  function closeMapPickBanner() {
    state.pickState = null;
    previewLayerGroup.clearLayers();
    document.getElementById("mapPickBanner").hidden = true;
  }

  function confirmPlaceSelection(idx, name, subtext, coords) {
    const p = state.participants[idx];
    if (!p) return;
    const cleanName = (name || "").trim() || `地图选点 (${coords.lat.toFixed(4)}, ${coords.lng.toFixed(4)})`;
    const fullLabel = subtext && !cleanName.includes(subtext) ? `${cleanName}（${subtext}）` : cleanName;

    p.address = cleanName;
    p.resolved_address = fullLabel;
    p.coords = { lat: Number(coords.lat.toFixed(6)), lng: Number(coords.lng.toFixed(6)) };

    const addrInput = document.querySelector(`.mp-addr-input[data-idx="${idx}"]`);
    if (addrInput) addrInput.value = cleanName;

    const panel = document.getElementById(`suggestPanel-${idx}`);
    if (panel) panel.hidden = true;

    closeMapPickBanner();
    updateParticipantStatusBar(idx);
    renderConfirmedOriginMarkers();
    markDirty(true);
    if (state.snapMode === "peek") setSheetSnap("half");
  }

  document.getElementById("btnCancelMapPick").addEventListener("click", () => {
    closeMapPickBanner();
    if (state.snapMode === "peek") setSheetSnap("half");
  });

  document.getElementById("btnConfirmMapPick").addEventListener("click", () => {
    if (!state.pickState) return;
    const { idx, name, subtext, coords } = state.pickState;
    confirmPlaceSelection(idx, name, subtext, coords);
  });

  map.on("click", async (e) => {
    if (!state.pickState) return;
    const idx = state.pickState.idx;
    const lat = Number(e.latlng.lat.toFixed(6));
    const lng = Number(e.latlng.lng.toFixed(6));
    showPreviewOnMap(idx, "正在识别所点位置...", `${lat.toFixed(5)}, ${lng.toFixed(5)}`, { lat, lng }, false);
    try {
      const rev = await fetchJson(`/api/places/reverse?lat=${lat}&lng=${lng}`);
      if (state.pickState && state.pickState.idx === idx) {
        showPreviewOnMap(idx, rev.name, rev.subtext, { lat, lng }, false);
      }
    } catch {
      if (state.pickState && state.pickState.idx === idx) {
        showPreviewOnMap(idx, `地图选点 (${lat.toFixed(4)}, ${lng.toFixed(4)})`, "WGS-84 精确坐标", { lat, lng }, false);
      }
    }
  });

  const suggestTimers = {};
  const suggestAbortControllers = {};

  async function searchParticipantPlaces(idx, forceOpen = false) {
    const p = state.participants[idx];
    const panel = document.getElementById(`suggestPanel-${idx}`);
    if (!p || !panel) return;
    const query = (p.address || "").trim();
    if (!query) {
      panel.hidden = true;
      if (forceOpen) {
        alert("请先在输入框输入地名关键字，或直接点「图上选」在地图上点选起点。");
      }
      return;
    }

    if (suggestAbortControllers[idx]) {
      suggestAbortControllers[idx].abort();
    }
    const ctrl = new AbortController();
    suggestAbortControllers[idx] = ctrl;

    panel.hidden = false;
    panel.innerHTML = `
      <div class="mp-suggest-head">
        <span>正在搜索「${escapeHtml(query)}」...</span>
        <button type="button" class="mp-suggest-close" data-close="${idx}">收起</button>
      </div>
    `;
    panel.querySelector("[data-close]")?.addEventListener("click", () => {
      panel.hidden = true;
    });

    try {
      const resp = await fetchJson("/api/places/suggest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: ctrl.signal,
        body: JSON.stringify({
          query,
          city: state.city,
          bias_coords: getBiasCoords(idx),
          limit: 8,
          runtime_keys: getActiveRuntimeKeys(),
        }),
      });

      const list = resp.suggestions || [];
      state.suggestionsByIndex[idx] = list;
      const pLabel = { amap: "高德", google: "Google", osm: "OSM", demo: "样本" }[resp.active_provider] || resp.active_provider;

      if (list.length === 0) {
        panel.innerHTML = `
          <div class="mp-suggest-head">
            <span>未找到「${escapeHtml(query)}」，试试切换左上角城市或用「图上选」</span>
            <button type="button" class="mp-suggest-close" data-close="${idx}">收起</button>
          </div>
        `;
        panel.querySelector("[data-close]")?.addEventListener("click", () => (panel.hidden = true));
        return;
      }

      panel.innerHTML = `
        <div class="mp-suggest-head">
          <span>点一项在地图预览位置，核对区县街道后点「确定」 (${escapeHtml(pLabel)})</span>
          <button type="button" class="mp-suggest-close" data-close="${idx}">收起</button>
        </div>
        <div class="mp-suggest-list">
          ${list
            .map(
              (c, cIdx) => `
            <div class="mp-cand-item" data-cidx="${cIdx}">
              <div class="mp-cand-main">
                <div class="mp-cand-title-row">
                  <span class="mp-cand-name">${escapeHtml(c.name)}</span>
                  ${c.category ? `<span class="mp-cand-tag">${escapeHtml(c.category)}</span>` : ""}
                </div>
                <div class="mp-cand-sub">${escapeHtml(c.subtext || "点击在地图查看具体位置")}</div>
              </div>
              <button type="button" class="mp-cand-confirm" data-confirm-cidx="${cIdx}">确定</button>
            </div>
          `
            )
            .join("")}
        </div>
      `;

      panel.querySelector("[data-close]")?.addEventListener("click", () => {
        panel.hidden = true;
      });

      panel.querySelectorAll(".mp-cand-item").forEach((row) => {
        row.addEventListener("click", (ev) => {
          if (ev.target.closest(".mp-cand-confirm")) return;
          const cIdx = Number(row.dataset.cidx);
          const cand = list[cIdx];
          if (!cand) return;
          panel.querySelectorAll(".mp-cand-item").forEach((r) => r.classList.remove("previewing"));
          row.classList.add("previewing");
          showPreviewOnMap(idx, cand.name, cand.subtext, cand.coords, false);
        });
      });

      panel.querySelectorAll(".mp-cand-confirm").forEach((btn) => {
        btn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const cIdx = Number(btn.dataset.confirmCidx);
          const cand = list[cIdx];
          if (!cand) return;
          confirmPlaceSelection(idx, cand.name, cand.subtext, cand.coords);
        });
      });
    } catch (err) {
      if (err.name === "AbortError") return;
      panel.innerHTML = `
        <div class="mp-suggest-head">
          <span style="color:#8f311c;">搜索失败：${escapeHtml(err.message)}</span>
          <button type="button" class="mp-suggest-close" data-close="${idx}">收起</button>
        </div>
      `;
      panel.querySelector("[data-close]")?.addEventListener("click", () => (panel.hidden = true));
    }
  }

  async function startMapTapPick(idx) {
    const p = state.participants[idx];
    const startCoords = p?.coords || getBiasCoords(idx);
    showPreviewOnMap(
      idx,
      p?.address || "点击地图选择起点位置",
      p?.resolved_address || "轻触地图任意位置即可移动选点，确认后点右侧「确定」",
      startCoords,
      true
    );
    if (!p?.coords) {
      try {
        const rev = await fetchJson(`/api/places/reverse?lat=${startCoords.lat}&lng=${startCoords.lng}`);
        if (state.pickState && state.pickState.idx === idx) {
          showPreviewOnMap(idx, rev.name, rev.subtext, startCoords, true);
        }
      } catch {}
    }
  }

  function renderParticipants() {
    const container = document.getElementById("participantsContainer");
    container.innerHTML = "";

    state.participants.forEach((p, idx) => {
      const color = COLORS[idx % COLORS.length];
      const item = document.createElement("div");
      item.className = "mp-origin-item";
      item.innerHTML = `
        <div class="mp-origin-top">
          <div class="mp-person-tag">
            <span class="mp-dot" style="background:${color}"></span>
            <input class="mp-name-edit" type="text" value="${escapeHtml(p.name)}" data-idx="${idx}" data-field="name" aria-label="姓名" />
          </div>
          <div style="display:flex;align-items:center;gap:4px;">
            <div class="mp-mode-pills">
              ${MODES.map(
                (m) => `
                <button type="button" class="mp-mode-btn ${p.transport_mode === m.val ? "active" : ""}" data-idx="${idx}" data-mode="${m.val}">
                  ${m.short}
                </button>
              `
              ).join("")}
            </div>
            ${
              state.participants.length > 2
                ? `<button type="button" class="mp-close-circle btn-rm-p" data-idx="${idx}" style="width:24px;height:24px;font-size:11px;">✕</button>`
                : ""
            }
          </div>
        </div>
        <div class="mp-addr-row">
          <input
            class="mp-addr-input"
            type="text"
            placeholder="输入地名搜索，点选候选后点确定"
            value="${escapeHtml(p.address)}"
            data-idx="${idx}"
            data-field="address"
            autocomplete="off"
          />
          <button type="button" class="mp-action-mini primary-mini btn-search-p" data-idx="${idx}">搜地点</button>
          <button type="button" class="mp-action-mini btn-mappick-p" data-idx="${idx}">图上选</button>
          <button type="button" class="mp-gps-btn" data-idx="${idx}">定位</button>
        </div>
        <div id="resolvedBar-${idx}" class="mp-resolved-bar" hidden></div>
        <div id="suggestPanel-${idx}" class="mp-suggest-panel" hidden></div>
        <input
          class="mp-note-input"
          type="text"
          placeholder="备注（比如：走几号线、要停车、忌口）"
          value="${escapeHtml(p.preference_note || "")}"
          data-idx="${idx}"
          data-field="preference_note"
        />
      `;
      container.appendChild(item);
      updateParticipantStatusBar(idx);
    });

    container.querySelectorAll("input").forEach((inp) => {
      let composing = false;
      inp.addEventListener("compositionstart", () => {
        composing = true;
      });
      inp.addEventListener("compositionend", (e) => {
        composing = false;
        handleParticipantFieldInput(e.target);
      });
      inp.addEventListener("input", (e) => {
        if (composing) return;
        handleParticipantFieldInput(e.target);
      });
      inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          const idx = Number(e.target.dataset.idx);
          if (e.target.dataset.field === "address") {
            searchParticipantPlaces(idx, true);
          } else {
            e.target.blur();
            computeRecommendations(true);
          }
        }
      });
    });

    container.querySelectorAll(".btn-search-p").forEach((b) => {
      b.addEventListener("click", () => searchParticipantPlaces(Number(b.dataset.idx), true));
    });

    container.querySelectorAll(".btn-mappick-p").forEach((b) => {
      b.addEventListener("click", () => startMapTapPick(Number(b.dataset.idx)));
    });

    container.querySelectorAll(".mp-mode-btn").forEach((b) => {
      b.addEventListener("click", () => {
        const idx = Number(b.dataset.idx);
        state.participants[idx].transport_mode = b.dataset.mode;
        renderParticipants();
        markDirty(true);
      });
    });

    container.querySelectorAll(".btn-rm-p").forEach((b) => {
      b.addEventListener("click", () => {
        state.participants.splice(Number(b.dataset.idx), 1);
        renderParticipants();
        renderConfirmedOriginMarkers();
        markDirty(true);
      });
    });

    container.querySelectorAll(".mp-gps-btn").forEach((b) => {
      b.addEventListener("click", () => usePhoneGps(Number(b.dataset.idx), b));
    });

    renderConfirmedOriginMarkers();
  }

  function handleParticipantFieldInput(target) {
    const idx = Number(target.dataset.idx);
    const field = target.dataset.field;
    state.participants[idx][field] = target.value;

    if (field === "address") {
      state.participants[idx].coords = null;
      state.participants[idx].resolved_address = "";
      updateParticipantStatusBar(idx);
      renderConfirmedOriginMarkers();

      clearTimeout(suggestTimers[idx]);
      const q = target.value.trim();
      if (q.length >= 2) {
        suggestTimers[idx] = setTimeout(() => searchParticipantPlaces(idx, false), 340);
      } else {
        const panel = document.getElementById(`suggestPanel-${idx}`);
        if (panel) panel.hidden = true;
      }
    }
    markDirty(true);
  }

  function usePhoneGps(idx, btn) {
    if (!navigator.geolocation) {
      alert("当前浏览器无法读取定位，请用 HTTPS 链接打开或点「图上选」。");
      return;
    }
    const orig = btn.textContent;
    btn.textContent = "定位中";
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const lat = Number(pos.coords.latitude.toFixed(6));
        const lng = Number(pos.coords.longitude.toFixed(6));
        btn.textContent = orig;
        let name = `当前位置 (${lat.toFixed(4)}, ${lng.toFixed(4)})`;
        let sub = "手机实时定位";
        try {
          const rev = await fetchJson(`/api/places/reverse?lat=${lat}&lng=${lng}`);
          if (rev?.name) {
            name = rev.name;
            sub = rev.subtext || sub;
          }
        } catch {}
        confirmPlaceSelection(idx, name, sub, { lat, lng });
      },
      (err) => {
        btn.textContent = orig;
        const httpsHint =
          window.location.protocol !== "https:" && state.publicHttpsUrl
            ? `\n请改用公网 HTTPS 链接打开以开启手机定位：${state.publicHttpsUrl}`
            : "";
        alert(`读取定位失败 (${err.message})${httpsHint}`);
      },
      { enableHighAccuracy: true, timeout: 9000 }
    );
  }

  async function computeRecommendations(autoSwitchToResults = false) {
    const cta = document.getElementById("btnCompute");
    const toast = document.getElementById("mapLoadingToast");
    cta.disabled = true;
    toast.hidden = false;
    cta.textContent = "计算中...";
    closeMapPickBanner();

    try {
      const runtimeKeys = getActiveRuntimeKeys();
      const payload = {
        participants: state.participants,
        city: state.city,
        venue_category: state.venueCategory,
        meetup_time: document.getElementById("meetupTime").value,
        fairness_mode: state.fairnessMode,
        shared_prompt: document.getElementById("sharedPrompt").value,
        limit: 6,
        runtime_keys: runtimeKeys,
      };

      let data;
      if (state.roomCode) {
        for (const p of state.participants) {
          await fetchJson(`/api/rooms/${state.roomCode}/join`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              participant: p,
              city: payload.city,
              venue_category: payload.venue_category,
              shared_prompt: payload.shared_prompt,
              fairness_mode: payload.fairness_mode,
              meetup_time: payload.meetup_time,
            }),
          });
        }
        const roomData = await fetchJson(`/api/rooms/${state.roomCode}/compute`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ runtime_keys: payload.runtime_keys }),
        });
        state.votes = roomData.votes || {};
        data = roomData.last_result;
      } else {
        data = await fetchJson("/api/recommend", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }

      state.lastResponse = data;
      state.selectedRankIndex = 0;
      (data.participants || []).forEach((rp, i) => {
        const cur = state.participants[i];
        if (cur && cur.address === rp.input_address) {
          cur.coords = rp.coords;
          cur.resolved_address = rp.resolved_address;
          updateParticipantStatusBar(i);
        }
      });
      const anyOpenSuggest = Array.from(document.querySelectorAll(".mp-suggest-panel")).some((el) => !el.hidden);
      if (!anyOpenSuggest) {
        markDirty(false);
      }
      originDraftLayer.clearLayers();
      renderResponse(data);

      if (autoSwitchToResults) {
        switchTab("results");
      }
    } catch (err) {
      showWarning(err.message || "计算失败");
      markDirty(true);
    } finally {
      cta.disabled = false;
      toast.hidden = true;
    }
  }

  function renderResponse(data) {
    showWarning(data.warnings && data.warnings.length ? data.warnings.join(" ") : "");

    const count = data.recommendations?.length || 0;
    const countBadge = document.getElementById("resultsCountBadge");
    countBadge.hidden = count === 0;
    countBadge.textContent = String(count);

    document.getElementById("topStatusSub").textContent = count > 0 ? `筛出 ${count} 个候选地点` : "按实际路程碰头";

    const banner = document.getElementById("intentSummaryCard");
    banner.hidden = false;
    const pNames = { osm: "OpenStreetMap 路网", amap: "高德地图路网", google: "Google Maps", demo: "离线样本" };
    document.getElementById("providerBadge").textContent = pNames[data.active_provider] || data.active_provider;
    document.getElementById("llmBadge").textContent = data.llm_active ? data.llm_source || "模型已参与筛选" : "规则筛选";
    document.getElementById("intentSummaryText").textContent = data.parsed_intent.compromise_summary;

    renderVenueCards(data);
    renderMapScene(data);
  }

  function renderVenueCards(data) {
    const listEl = document.getElementById("resultsList");
    listEl.innerHTML = "";

    if (!data.recommendations || data.recommendations.length === 0) {
      listEl.innerHTML = `<div class="mp-section-card">这一带没搜到合适地点，试试切回「不限地标」或点「搜地点」重新锁定起点。</div>`;
      return;
    }

    data.recommendations.forEach((item, idx) => {
      const v = item.venue;
      const votes = state.votes[v.place_id] || [];
      const totalMins = item.legs.reduce((acc, l) => acc + Math.max(1, l.duration_minutes), 0) || 1;
      const card = document.createElement("article");
      card.className = `mp-venue-card ${idx === state.selectedRankIndex ? "selected" : ""}`;
      card.innerHTML = `
        <div class="mp-v-head">
          <div>
            <span class="mp-rank-badge ${idx === 0 ? "top1" : ""}">#${item.rank}</span>
            <h3 class="mp-v-name">${escapeHtml(v.name)}</h3>
            <div class="mp-v-addr">
              ${escapeHtml(v.categories.slice(0, 2).join(" · "))}${v.address ? ` · ${escapeHtml(v.address)}` : ""}
            </div>
          </div>
          <div class="mp-score-box">
            <div class="mp-score-val">${Math.round(item.duration_gap_minutes)}<span style="font-size:11px;font-weight:500;">分</span></div>
            <div class="mp-score-lbl">双方时差</div>
          </div>
        </div>

        <div class="commute-bar-track" aria-hidden="true">
          ${item.legs
            .map(
              (leg, pIdx) =>
                `<div class="commute-bar-seg" style="width:${((leg.duration_minutes / totalMins) * 100).toFixed(1)}%;background:${
                  COLORS[pIdx % COLORS.length]
                };"></div>`
            )
            .join("")}
        </div>

        <div class="mp-legs-row">
          ${item.legs
            .map(
              (leg, pIdx) => `
              <div class="mp-leg-box">
                <span style="color:${COLORS[pIdx % COLORS.length]};font-weight:700;">● ${escapeHtml(leg.participant_name)}</span>
                <span class="mp-leg-mins"> ${Math.round(leg.duration_minutes)} 分钟</span><br/>
                <span style="color:#5c646d;font-size:11px;">${
                  leg.suggested_departure ? `${leg.suggested_departure} 出发 · ` : ""
                }${escapeHtml(leg.route_summary)}</span>
              </div>
            `
            )
            .join("")}
        </div>

        <div class="mp-ai-reason">
          ${escapeHtml(item.ai_reason)}
          ${item.transport_tip ? `<div style="color:#5c646d;margin-top:3px;">${escapeHtml(item.transport_tip)}</div>` : ""}
        </div>

        <div class="mp-v-btns">
          <button type="button" class="mp-btn-fill btn-nav" data-idx="${idx}">去这里（导航）</button>
          <button type="button" class="mp-btn-outline btn-vote" data-place="${escapeHtml(v.place_id)}">
            赞成 (${votes.length})
          </button>
        </div>
      `;

      card.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        state.selectedRankIndex = idx;
        renderVenueCards(data);
        drawSelectedVenueRoutes(data, idx);
        map.panTo([v.coords.lat, v.coords.lng], { animate: true });
      });

      listEl.appendChild(card);
    });

    listEl.querySelectorAll(".btn-nav").forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        openNavigationSheet(Number(b.dataset.idx));
      });
    });

    listEl.querySelectorAll(".btn-vote").forEach((b) => {
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        const placeId = b.dataset.place;
        if (state.roomCode) {
          try {
            const room = await fetchJson(`/api/rooms/${state.roomCode}/vote`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ participant_id: state.participants[0]?.id || "p1", place_id: placeId }),
            });
            state.votes = room.votes || {};
          } catch {}
        } else {
          const cur = state.votes[placeId] || [];
          state.votes[placeId] = cur.includes("me") ? [] : ["me"];
        }
        renderVenueCards(data);
      });
    });
  }

  let currentBoundsPoints = [];

  function fitMapToCurrentBounds() {
    if (currentBoundsPoints.length < 2) return;
    const sheetH = sheetEl.getBoundingClientRect().height;
    map.fitBounds(currentBoundsPoints, {
      paddingTopLeft: [28, 70],
      paddingBottomRight: [28, Math.min(window.innerHeight * 0.62, sheetH + 24)],
      animate: true,
    });
  }

  function renderMapScene(data) {
    mapLayerGroup.clearLayers();
    currentBoundsPoints = [];

    L.circleMarker([data.geometric_midpoint.lat, data.geometric_midpoint.lng], {
      radius: 5,
      color: "#5c646d",
      fillColor: "#94a3b8",
      fillOpacity: 0.65,
    })
      .bindPopup("几何直线中点（仅供参考）")
      .addTo(mapLayerGroup);

    data.corridor_anchors.forEach((anchor, i) => {
      L.circle([anchor.coords.lat, anchor.coords.lng], {
        radius: i === 0 ? data.search_radius_m : data.search_radius_m * 0.65,
        color: "#0F6E56",
        weight: 1.2,
        dashArray: "5,5",
        fillColor: "#0F6E56",
        fillOpacity: i === 0 ? 0.08 : 0.03,
      }).addTo(mapLayerGroup);
    });

    data.participants.forEach((p, idx) => {
      const color = COLORS[idx % COLORS.length];
      const pt = [p.coords.lat, p.coords.lng];
      currentBoundsPoints.push(pt);
      const icon = L.divIcon({
        className: "p-pin",
        html: `<div style="background:${color};color:#fff;padding:3px 9px;border-radius:6px;font-weight:700;font-size:12px;white-space:nowrap;border:1.5px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.22);">${escapeHtml(
          p.name
        )}</div>`,
        iconAnchor: [28, 14],
      });
      L.marker(pt, { icon }).bindPopup(`<strong>${escapeHtml(p.name)}</strong><br/>${escapeHtml(p.resolved_address)}`).addTo(mapLayerGroup);
    });

    data.recommendations.forEach((item, idx) => {
      const v = item.venue;
      const pt = [v.coords.lat, v.coords.lng];
      currentBoundsPoints.push(pt);
      const icon = L.divIcon({
        className: "v-pin",
        html: `<div style="background:${idx === 0 ? "#0F6E56" : "#14171A"};color:#fff;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:12px;border:2px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,0.28);">${
          item.rank
        }</div>`,
        iconAnchor: [13, 13],
      });
      L.marker(pt, { icon })
        .addTo(mapLayerGroup)
        .on("click", () => {
          state.selectedRankIndex = idx;
          renderVenueCards(data);
          drawSelectedVenueRoutes(data, idx);
          switchTab("results");
        });
    });

    drawSelectedVenueRoutes(data, state.selectedRankIndex);
    fitMapToCurrentBounds();
  }

  function drawSelectedVenueRoutes(data, rankIdx) {
    routeLayerGroup.clearLayers();
    const chosen = data.recommendations[rankIdx];
    if (!chosen) return;
    const destPt = [chosen.venue.coords.lat, chosen.venue.coords.lng];
    data.participants.forEach((p, idx) => {
      L.polyline([[p.coords.lat, p.coords.lng], destPt], {
        color: COLORS[idx % COLORS.length],
        weight: 3.2,
        opacity: 0.85,
        dashArray: p.transport_mode === "transit" ? "7,5" : null,
      }).addTo(routeLayerGroup);
    });
  }

  function openNavigationSheet(rankIdx) {
    const rec = state.lastResponse?.recommendations?.[rankIdx];
    if (!rec) return;
    state.activeNavVenue = rec;
    state.activeNavLegIndex = 0;
    document.getElementById("navModalTitle").textContent = rec.venue.name;
    document.getElementById("navModalSubtitle").textContent = rec.venue.address;
    renderNavSheetContent();
    document.getElementById("navModal").hidden = false;
  }

  function renderNavSheetContent() {
    const rec = state.activeNavVenue;
    const tabs = document.getElementById("navParticipantTabs");
    tabs.innerHTML = rec.legs
      .map(
        (leg, i) => `
        <button type="button" class="fair-btn ${i === state.activeNavLegIndex ? "active" : ""}" data-leg="${i}">
          从 ${escapeHtml(leg.participant_name)} 出发 (${Math.round(leg.duration_minutes)}分)
        </button>
      `
      )
      .join("");
    tabs.querySelectorAll(".fair-btn").forEach((b) => {
      b.addEventListener("click", () => {
        state.activeNavLegIndex = Number(b.dataset.leg);
        renderNavSheetContent();
      });
    });

    const leg = rec.legs[state.activeNavLegIndex];
    const links = leg.navigation_links;
    const items = [
      { k: "amap", name: "高德地图", sub: "带入起点与出行方式" },
      { k: "apple_maps", name: "Apple 地图", sub: "iOS 系统地图" },
      { k: "google_maps", name: "Google Maps", sub: "海外及通用路线" },
      { k: "baidu_maps", name: "百度地图", sub: "备选路线" },
    ];
    document.getElementById("navLinksGrid").innerHTML = items
      .filter((x) => links[x.k])
      .map(
        (x) => `
        <a class="mp-nav-item" href="${links[x.k]}" target="_blank" rel="noopener noreferrer">
          <span>${x.name}</span>
          <small>${x.sub} ›</small>
        </a>
      `
      )
      .join("");
  }

  function getBestShareBaseUrl() {
    if (window.location.protocol === "https:") return window.location.origin;
    if (state.publicHttpsUrl) return state.publicHttpsUrl;
    if (state.lanUrl) return state.lanUrl;
    return window.location.origin;
  }

  function buildShareLink() {
    const base = getBestShareBaseUrl();
    return state.roomCode ? `${base}/?room=${encodeURIComponent(state.roomCode)}` : `${base}/`;
  }

  function renderQrCode(url) {
    const box = document.getElementById("qrCodeBox");
    document.getElementById("publicUrlInput").value = url;
    if (typeof window.qrcode === "function") {
      try {
        const qr = window.qrcode(0, "M");
        qr.addData(url);
        qr.make();
        box.innerHTML = qr.createSvgTag({ cellSize: 3, margin: 1, scalable: true });
        return;
      } catch {}
    }
    box.innerHTML = `<span style="font-size:11px;color:#5c646d;text-align:center;">复制右侧链接打开</span>`;
  }

  async function refreshAccessAndHealthInfo() {
    try {
      const [accResp, healthResp] = await Promise.all([fetch("/api/access-info"), fetch("/api/health")]);
      if (accResp.ok) {
        const acc = await accResp.json();
        state.publicHttpsUrl = acc.public_https_url || "";
        state.lanUrl = acc.lan_url || "";
        state.isLocalAdmin = Boolean(acc.is_local_admin);
        renderQrCode(buildShareLink());
      }
      if (healthResp.ok) {
        const h = await healthResp.json();
        state.isLocalAdmin = Boolean(h.is_local_admin);
        const localK = loadRuntimeKeys();
        const hasAmap = Boolean(h.has_amap_key || localK.amap_api_key);
        const hasGoogle = Boolean(h.has_google_key || localK.google_maps_api_key);
        const hasLlm = Boolean(h.has_llm_key || localK.llm_api_key || h.has_qwen_env || h.has_zhipu_free_env);

        const badgeBox = document.getElementById("apiStatusBadges");
        badgeBox.innerHTML = `
          <span class="api-chip ${hasAmap ? "ok" : ""}">高德 Web服务：${hasAmap ? "已接入" : "待填入 Key"}</span>
          <span class="api-chip ${hasGoogle ? "ok" : ""}">Google Maps：${hasGoogle ? "已接入" : "待填入 Key"}</span>
          <span class="api-chip ok">OSM 路网：就绪</span>
          <span class="api-chip ${hasLlm ? "ok" : ""}">大模型：${escapeHtml(h.llm_model || "已就绪")}</span>
        `;

        document.getElementById("serverStatusHint").textContent = hasAmap && hasGoogle
          ? "高德 + Google 双引擎已就绪：国内地址自动走高德，海外地址自动走 Google。"
          : hasAmap
          ? "高德 Web服务已就绪；再填入 Google Maps Key 即可同时覆盖海外地点。"
          : hasGoogle
          ? "Google Maps 已就绪；再填入高德 Web服务 Key 即可增强国内地铁换乘与 POI。"
          : "当前使用 OpenStreetMap 免费路网；在下方填入高德与 Google Key 可一键升级双引擎。";

        const saveEnvBtn = document.getElementById("btnSaveLocalEnv");
        const envNotice = document.getElementById("localEnvNotice");
        if (state.isLocalAdmin) {
          saveEnvBtn.hidden = false;
          try {
            const locCfg = await fetchJson("/api/local-config");
            if (!localK.llm_base_url) document.getElementById("cfgLlmBaseUrl").value = locCfg.llm_base_url || "";
            if (!localK.llm_model) document.getElementById("cfgLlmModel").value = locCfg.llm_model || "";
            if (!localK.map_provider) document.getElementById("cfgMapProvider").value = locCfg.map_provider || "auto";
            if (!localK.llm_provider) document.getElementById("cfgLlmProvider").value = locCfg.llm_provider || "auto";
            envNotice.hidden = false;
            envNotice.innerHTML = `本机 <code>.env</code> 当前状态：高德 <strong>${
              locCfg.amap_masked || "未填"
            }</strong> · Google <strong>${locCfg.google_masked || "未填"}</strong> · 大模型 Key <strong>${
              locCfg.llm_masked || "未填"
            }</strong>（在本机直接点「写入本机 .env 并验证」即可持久生效，且不会泄露给外网访客）`;
          } catch {}
        } else {
          saveEnvBtn.hidden = true;
          envNotice.hidden = true;
        }
      }
    } catch {}
  }

  async function runVerifyKeys() {
    const box = document.getElementById("verifyReportBox");
    box.hidden = false;
    box.textContent = "正在实测高德、Google Maps 与大模型连通性...";
    try {
      const formOverrides = collectSettingsFormValues();
      const cleaned = Object.fromEntries(Object.entries(formOverrides).filter(([_, v]) => v !== ""));
      const rep = await fetchJson("/api/verify-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ runtime_keys: cleaned }),
      });
      const fmtLine = (title, item) => {
        if (!item) return "";
        const icon = item.ok ? "✅" : item.configured ? "❌" : "⚪";
        const ms = item.latency_ms ? ` (${item.latency_ms}ms)` : "";
        return `<div>${icon} <strong>${title}</strong>${ms}：${escapeHtml(item.detail || "")}</div>`;
      };
      box.innerHTML = [
        fmtLine("高德 Web服务", rep.amap),
        fmtLine("Google Maps", rep.google),
        fmtLine("大模型通道", rep.llm),
      ].join("");
    } catch (err) {
      box.innerHTML = `<div style="color:#8f311c;">检测异常：${escapeHtml(err.message)}</div>`;
    }
  }

  function collectSettingsFormValues() {
    return {
      map_provider: document.getElementById("cfgMapProvider").value,
      llm_provider: document.getElementById("cfgLlmProvider").value,
      amap_api_key: document.getElementById("cfgAmapKey").value.trim(),
      google_maps_api_key: document.getElementById("cfgGoogleKey").value.trim(),
      kimi_web_token: document.getElementById("cfgKimiWebToken").value.trim(),
      free_web_gateway_url: document.getElementById("cfgFreeWebGateway").value.trim(),
      llm_api_key: document.getElementById("cfgLlmKey").value.trim(),
      llm_base_url: document.getElementById("cfgLlmBaseUrl").value.trim(),
      llm_model: document.getElementById("cfgLlmModel").value.trim(),
    };
  }

  async function createAndShareRoom() {
    if (!state.roomCode) {
      try {
        const room = await fetchJson("/api/rooms", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: "选个折中碰面点",
            city: state.city,
            venue_category: state.venueCategory,
            fairness_mode: state.fairnessMode,
            meetup_time: document.getElementById("meetupTime").value,
            shared_prompt: document.getElementById("sharedPrompt").value,
            host: state.participants[0],
          }),
        });
        for (let i = 1; i < state.participants.length; i++) {
          await fetchJson(`/api/rooms/${room.room_code}/join`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ participant: state.participants[i] }),
          });
        }
        activateRoomBadge(room.room_code);
      } catch (err) {
        showWarning(err.message || "创建房间失败");
        return;
      }
    }
    await refreshAccessAndHealthInfo();
    const shareUrl = buildShareLink();
    renderQrCode(shareUrl);

    if (navigator.share) {
      try {
        await navigator.share({
          title: "填一下你的出发地，看看我们约在哪碰头最顺路",
          url: shareUrl,
        });
        return;
      } catch {}
    }
    await navigator.clipboard.writeText(shareUrl);
    switchTab("profile");
    alert(`房间链接已复制：\n${shareUrl}`);
  }

  function activateRoomBadge(code) {
    state.roomCode = code;
    const pill = document.getElementById("roomCodePill");
    pill.hidden = false;
    pill.textContent = `#${code}`;
    const u = new URL(window.location.href);
    u.searchParams.set("room", code);
    window.history.replaceState({}, "", u.toString());
  }

  async function loadRoomFromUrl() {
    const code = new URLSearchParams(window.location.search).get("room");
    if (!code) return { inRoom: false, hadRoomParam: false };
    try {
      const room = await fetchJson(`/api/rooms/${encodeURIComponent(code)}`);
      activateRoomBadge(room.room_code);
      state.city = room.city || "";
      document.getElementById("citySelect").value = state.city;
      state.participants =
        room.participants.length >= 2
          ? room.participants
          : [
              ...room.participants,
              { id: "p2", name: "朋友 (我)", address: "", resolved_address: "", coords: null, transport_mode: "transit", preference_note: "" },
            ];
      state.venueCategory = room.venue_category || "any";
      state.fairnessMode = room.fairness_mode || "fair";
      state.votes = room.votes || {};
      document.getElementById("meetupTime").value = room.meetup_time || "19:00";
      document.getElementById("sharedPrompt").value = room.shared_prompt || "";
      document.querySelectorAll(".type-pill").forEach((s) => s.classList.toggle("active", s.dataset.cat === state.venueCategory));
      document.querySelectorAll("#tabPaneResults [data-fairness]").forEach((b) => {
        b.classList.toggle("active", b.dataset.fairness === state.fairnessMode);
      });
      renderParticipants();
      if (room.last_result) {
        state.lastResponse = room.last_result;
        renderResponse(room.last_result);
        switchTab("results");
      } else {
        showWarning(`已进入房间 #${room.room_code}。输入你的起点并点选「确定」后，点下方「找折中地点」。`);
      }
      return { inRoom: true, hadRoomParam: true };
    } catch (err) {
      showWarning(err.message || `房间 ${code} 打不开，请让发起人重新分享链接`);
      return { inRoom: false, hadRoomParam: true };
    }
  }

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    state.deferredInstallPrompt = e;
  });

  async function triggerHomeScreenInstall() {
    if (state.deferredInstallPrompt) {
      state.deferredInstallPrompt.prompt();
      await state.deferredInstallPrompt.userChoice;
      state.deferredInstallPrompt = null;
      return;
    }
    switchTab("profile");
    alert("iPhone 用 Safari 点底部分享按钮选「添加到主屏幕」；Android 用 Chrome 点右上角菜单选「安装应用」。");
  }

  function loadRuntimeKeys() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function syncSettingsInputs() {
    const cfg = loadRuntimeKeys();
    document.getElementById("cfgMapProvider").value = cfg.map_provider || "auto";
    document.getElementById("cfgLlmProvider").value = cfg.llm_provider || "auto";
    document.getElementById("cfgAmapKey").value = cfg.amap_api_key || "";
    document.getElementById("cfgGoogleKey").value = cfg.google_maps_api_key || "";
    document.getElementById("cfgKimiWebToken").value = cfg.kimi_web_token || "";
    document.getElementById("cfgFreeWebGateway").value = cfg.free_web_gateway_url || "";
    document.getElementById("cfgLlmKey").value = cfg.llm_api_key || "";
    document.getElementById("cfgLlmBaseUrl").value = cfg.llm_base_url || "";
    document.getElementById("cfgLlmModel").value = cfg.llm_model || "";
  }

  document.getElementById("citySelect").addEventListener("change", (e) => {
    state.city = e.target.value;
    document.querySelectorAll(".preset-chip").forEach((c) => c.classList.remove("active"));
    if (state.city && CITY_CENTERS[state.city]) {
      map.setView(CITY_CENTERS[state.city], 11, { animate: true });
    }
    markDirty(true);
  });

  document.getElementById("btnAddPerson").addEventListener("click", () => {
    if (state.participants.length >= 8) return;
    const n = state.participants.length + 1;
    state.participants.push({
      id: `p${n}`,
      name: `同行人 ${n}`,
      address: "",
      resolved_address: "",
      coords: null,
      transport_mode: "transit",
      preference_note: "",
    });
    renderParticipants();
    markDirty(true);
  });

  document.querySelectorAll(".preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".preset-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      const pr = PRESETS[chip.dataset.preset];
      state.city = pr.city || "";
      document.getElementById("citySelect").value = state.city;
      state.participants = structuredClone(pr.participants);
      state.venueCategory = pr.category;
      state.fairnessMode = pr.fairness;
      document.querySelectorAll(".type-pill").forEach((s) => s.classList.toggle("active", s.dataset.cat === pr.category));
      document.querySelectorAll("#tabPaneResults [data-fairness]").forEach((b) => {
        b.classList.toggle("active", b.dataset.fairness === pr.fairness);
      });
      document.getElementById("meetupTime").value = pr.meetupTime;
      document.getElementById("sharedPrompt").value = pr.sharedPrompt;
      renderParticipants();
      computeRecommendations(true);
    });
  });

  document.querySelectorAll(".type-pill").forEach((item) => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".type-pill").forEach((i) => i.classList.remove("active"));
      item.classList.add("active");
      state.venueCategory = item.dataset.cat;
      markDirty(true);
    });
  });

  document.querySelectorAll(".vibe-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      chip.classList.toggle("active");
      const activeVibes = Array.from(document.querySelectorAll(".vibe-chip.active")).map((c) => c.dataset.vibe);
      document.getElementById("sharedPrompt").value = activeVibes.join("，");
      markDirty(true);
    });
  });

  document.querySelectorAll("#tabPaneResults [data-fairness]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#tabPaneResults [data-fairness]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.fairnessMode = btn.dataset.fairness;
      computeRecommendations(false);
    });
  });

  document.getElementById("btnCompute").addEventListener("click", () => computeRecommendations(true));
  document.getElementById("btnFitBounds").addEventListener("click", fitMapToCurrentBounds);
  document.getElementById("btnSwitchTile").addEventListener("click", () => {
    if (state.tileMode === "osm") {
      map.removeLayer(osmTiles);
      esriTiles.addTo(map);
      state.tileMode = "esri";
    } else {
      map.removeLayer(esriTiles);
      osmTiles.addTo(map);
      state.tileMode = "osm";
    }
  });

  document.getElementById("btnCapsuleInvite").addEventListener("click", createAndShareRoom);
  document.getElementById("btnShareRoom").addEventListener("click", createAndShareRoom);
  document.getElementById("btnCapsuleInstall").addEventListener("click", triggerHomeScreenInstall);
  document.getElementById("btnTriggerInstall").addEventListener("click", triggerHomeScreenInstall);

  document.getElementById("btnCopyPublicUrl").addEventListener("click", async () => {
    const val = document.getElementById("publicUrlInput").value;
    await navigator.clipboard.writeText(val);
    alert(`链接已复制：\n${val}`);
  });

  document.getElementById("btnCloseNavModal").addEventListener("click", () => {
    document.getElementById("navModal").hidden = true;
  });
  document.getElementById("navModal").addEventListener("click", (e) => {
    if (e.target.id === "navModal") document.getElementById("navModal").hidden = true;
  });

  document.getElementById("btnNativeShareSpot").addEventListener("click", async () => {
    const rec = state.activeNavVenue;
    if (!rec) return;
    const leg = rec.legs[state.activeNavLegIndex];
    const txt = `约在这里碰面：${rec.venue.name}（${rec.venue.address}）\n${rec.ai_reason}\n高德路线：${leg.navigation_links.amap}`;
    if (navigator.share) {
      try {
        await navigator.share({ title: rec.venue.name, text: txt, url: leg.navigation_links.amap });
        return;
      } catch {}
    }
    await navigator.clipboard.writeText(txt);
    alert("地点与路线链接已复制。");
  });

  document.getElementById("btnVerifyKeys").addEventListener("click", runVerifyKeys);

  document.getElementById("btnSaveLocalEnv").addEventListener("click", async () => {
    try {
      const vals = collectSettingsFormValues();
      await fetchJson("/api/local-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(vals),
      });
      document.getElementById("cfgAmapKey").value = "";
      document.getElementById("cfgGoogleKey").value = "";
      document.getElementById("cfgLlmKey").value = "";
      await refreshAccessAndHealthInfo();
      await runVerifyKeys();
      markDirty(true);
    } catch (err) {
      alert(`写入本机 .env 失败：${err.message}`);
    }
  });

  document.getElementById("btnSaveSettings").addEventListener("click", async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(collectSettingsFormValues()));
    await refreshAccessAndHealthInfo();
    await runVerifyKeys();
    computeRecommendations(true);
  });

  document.getElementById("btnClearSettings").addEventListener("click", async () => {
    localStorage.removeItem(STORAGE_KEY);
    syncSettingsInputs();
    await refreshAccessAndHealthInfo();
    computeRecommendations(true);
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  syncSettingsInputs();
  renderParticipants();
  refreshAccessAndHealthInfo();
  loadRoomFromUrl().then(({ inRoom, hadRoomParam }) => {
    if (!inRoom && !hadRoomParam) computeRecommendations(false);
  });
})();
