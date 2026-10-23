"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const statusBadge = byId("status-badge");
  const video = byId("video-feed");
  const toggle = byId("toggle-playback");
  const restart = byId("restart-playback");
  let latest = null;
  let connected = false;
  let controlPending = false;
  let controlVersion = 0;
  let streamStarted = false;
  let streamFailed = false;
  let spacesKey = "";
  let streamIdentity = "";

  function setText(id, value) {
    const element = byId(id);
    const text = String(value);
    if (element.textContent !== text) element.textContent = text;
  }

  function number(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function clock(value) {
    const seconds = Math.max(0, Math.floor(number(value) ?? 0));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function showError(message) {
    const banner = byId("error-banner");
    banner.hidden = !message;
    if (banner.textContent !== (message || "")) banner.textContent = message || "";
  }

  function updateControls() {
    const ready = connected && latest && ["running", "paused"].includes(latest.status);
    toggle.disabled = !ready || controlPending;
    restart.disabled = !ready || controlPending;
    const paused = latest && latest.status === "paused";
    setText("toggle-label", paused ? "Resume" : "Pause");
    toggle.setAttribute("aria-label", paused ? "Resume video detection" : "Pause video detection");
    toggle.setAttribute("aria-busy", String(controlPending));
    byId("toggle-icon").innerHTML = paused
      ? '<path d="m8 5 11 7-11 7z"/>'
      : '<path d="M8 5v14M16 5v14"/>';
  }

  function placeholder(title, detail, loading) {
    byId("feed-placeholder").hidden = false;
    byId("feed-loader").hidden = !loading;
    byId("feed-caption").hidden = true;
    setText("feed-message", title);
    setText("feed-detail", detail);
  }

  function renderSpaces(spaces, ready) {
    const safeSpaces = ready && Array.isArray(spaces) ? spaces.filter((space) =>
      space && Number.isInteger(space.id) && (typeof space.occupied === "boolean" || space.occupied === null)) : [];
    const key = JSON.stringify(safeSpaces);
    if (key !== spacesKey) {
      spacesKey = key;
      const fragment = document.createDocumentFragment();
      safeSpaces.forEach((space) => {
        const tile = document.createElement("li");
        const labelState = space.occupied === null ? "checking" : (space.occupied ? "occupied" : "available");
        tile.className = `space-tile ${labelState}`;
        tile.setAttribute("aria-label", `Space ${space.id}: ${labelState}`);
        tile.title = `Space ${space.id} · ${labelState}`;
        const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        icon.setAttribute("viewBox", "0 0 24 24");
        icon.setAttribute("aria-hidden", "true");
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", space.occupied === null ? "M12 4v8l4 4" : (space.occupied ? "m5 10 2-5h10l2 5M4 10h16v8H4zM7 18v2m10-2v2M7 14h1m8 0h1" : "m6 12 4 4 8-8"));
        icon.appendChild(path);
        const label = document.createElement("span");
        label.textContent = String(space.id).padStart(2, "0");
        tile.append(icon, label);
        fragment.appendChild(tile);
      });
      byId("spaces-list").replaceChildren(fragment);
    }
    byId("spaces-empty").hidden = safeSpaces.length > 0;
    setText("zone-count", safeSpaces.length || "—");
  }

  function render(data) {
    latest = data;
    connected = true;
    document.body.classList.remove("connection-lost");
    statusBadge.dataset.state = data.status;
    const labels = { loading: "Starting detector", running: "Detection running", paused: "Detection paused", needs_zones: "Set up parking zones", error: "Detector unavailable" };
    setText("status-text", labels[data.status]);
    const ready = ["running", "paused"].includes(data.status);
    const previewReady = ready || data.status === "needs_zones";
    setText("source-name", data.source || "Parking video");
    byId("setup-notice").hidden = data.status !== "needs_zones";
    setText("stability-note", `Detection stability: ${data.smoothing || "balanced"}. ${data.pending || 0} spaces checking initial status. Changes require consecutive frames.`);
    const identity = data.stream_id || `${data.source_id}:${data.revision}`;
    if (identity !== streamIdentity) {
      streamIdentity = identity;
      streamStarted = false;
      video.removeAttribute("src");
      video.hidden = true;
    }
    const total = number(data.total);
    const occupied = ready ? number(data.occupied) : null;
    const available = ready ? number(data.available) : null;
    setText("total-count", total === null ? "—" : total);
    setText("occupied-count", occupied === null ? "—" : occupied);
    setText("available-count", available === null ? "—" : available);
    setText("inference-fps", ready && number(data.fps) !== null ? Math.max(0, data.fps).toFixed(1) : "—");
    setText("compute-device", data.device || "Initializing");
    setText("model-name", data.model || "YOLOv8n");
    setText("confidence-threshold", number(data.confidence) === null ? "—" : `${Math.round(data.confidence * 100)}%`);
    setText("current-time", clock(data.position_seconds));
    setText("duration-time", number(data.duration_seconds) > 0 ? clock(data.duration_seconds) : "--:--");
    const progress = number(data.duration_seconds) > 0 ? (data.position_seconds / data.duration_seconds) * 100 : 0;
    byId("video-progress").value = Number.isFinite(progress) ? Math.min(100, Math.max(0, progress)) : 0;
    const percent = total > 0 && occupied !== null ? Math.round((occupied / total) * 100) : null;
    setText("occupancy-percent", percent === null ? "—" : `${percent}%`);
    byId("occupancy-progress").value = percent ?? 0;
    setText("occupancy-description", occupied === null || total === null ? "Waiting for detection data" : `${occupied} of ${total} spaces occupied${data.pending ? ` · ${data.pending} checking` : ""}`);
    renderSpaces(data.spaces, ready);

    if (data.status === "error") {
      showError(data.error || "The detector could not start. Check the server output for details.");
      placeholder("Detector unavailable", "The server reported a problem. Details are shown above.", false);
    } else if (!previewReady) {
      showError(null);
      placeholder("Preparing your parking view", "Loading the model and the first video frame…", true);
    } else {
      showError(null);
      if (!streamStarted || streamFailed) {
        streamStarted = true;
        streamFailed = false;
        video.src = `/video_feed?connection=${Date.now()}`;
      }
      video.hidden = false;
      byId("feed-placeholder").hidden = true;
      byId("feed-caption").hidden = false;
      setText("feed-caption-text", data.status === "needs_zones" ? "Preview · draw parking zones to start" : (data.status === "paused" ? "Detection paused" : "Detection running"));
    }
    updateControls();
    document.dispatchEvent(new CustomEvent("parking-status", {detail: data}));
  }

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `Server returned HTTP ${response.status}`);
      if (!data || !["loading", "running", "paused", "needs_zones", "error"].includes(data.status)) throw new Error("The server returned an unexpected response.");
      return data;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function disconnected() {
    connected = false;
    document.body.classList.add("connection-lost");
    statusBadge.dataset.state = "offline";
    setText("status-text", "Connection interrupted");
    showError("Cannot reach the local detector. Check that the Python server is running. Reconnecting automatically…");
    placeholder("Connection interrupted", "Reconnecting to the local server. Any displayed counts are from the last update.", false);
    updateControls();
  }

  async function poll() {
    const version = controlVersion;
    try {
      if (!controlPending) {
        const data = await api("/api/status");
        if (version === controlVersion) render(data);
      }
    } catch (_) {
      if (version === controlVersion) disconnected();
    } finally {
      window.setTimeout(poll, 1000);
    }
  }

  async function control(action) {
    if (controlPending || !connected || !latest || !["running", "paused"].includes(latest.status)) return;
    controlPending = true;
    controlVersion += 1;
    updateControls();
    try {
      render(await api("/api/control", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) }));
    } catch (error) {
      showError(`Could not ${action} the video. ${error.name === "AbortError" ? "The server took too long to respond." : error.message}`);
    } finally {
      controlPending = false;
      updateControls();
    }
  }

  toggle.addEventListener("click", () => control(latest && latest.status === "paused" ? "resume" : "pause"));
  restart.addEventListener("click", () => control("restart"));
  document.addEventListener("parking-refresh", async () => {
    controlVersion += 1;
    streamStarted = false;
    try { render(await api("/api/status")); } catch (_) { disconnected(); }
  });
  video.addEventListener("error", () => {
    streamFailed = true;
    placeholder("Reconnecting video preview", "The detector is still being checked. Retrying the video stream…", false);
  });
  poll();
})();
