"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const dialog = $("zone-editor"), canvas = $("zone-canvas");
  const sourceSelect = $("source-select");
  let activeId = null, busy = false, record = null, zones = [], selected = -1;
  let pending = [], drawing = false, history = [], drag = null, imageReady = false, saving = false;
  const clone = (value) => JSON.parse(JSON.stringify(value));

  async function request(url, options = {}) {
    const response = await fetch(url, {...options, cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The request failed. Please try again.");
    return data;
  }
  const post = (url, data) => request(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  function message(id, text) { $(id).textContent = text || ""; $(id).hidden = !text; }
  function busyState(value) {
    busy = value;
    sourceSelect.disabled = value;
    $("upload-video").disabled = value;
    $("edit-zones").disabled = value || !activeId;
  }
  async function library() {
    const data = await request("/api/sources");
    sourceSelect.replaceChildren(...data.sources.map((source) => {
      const option = document.createElement("option");
      option.value = source.id;
      option.textContent = `${source.name} · ${source.zones.length} spaces`;
      return option;
    }));
    activeId = data.active_id;
    sourceSelect.value = activeId;
    busyState(busy);
  }
  async function activate(id) {
    await post("/api/sources/activate", {source_id: id});
    activeId = id;
    document.dispatchEvent(new Event("parking-refresh"));
  }
  sourceSelect.addEventListener("change", async () => {
    const id = sourceSelect.value;
    busyState(true);
    message("source-message", "Opening video…");
    try { await activate(id); message("source-message", null); }
    catch (error) { sourceSelect.value = activeId; message("source-message", error.message); }
    finally { busyState(false); }
  });
  $("upload-video").addEventListener("click", () => $("video-file").click());
  $("video-file").addEventListener("change", async () => {
    const file = $("video-file").files[0];
    if (!file) return;
    if (file.size > 500 * 1024 * 1024) {
      message("source-message", "Choose a video smaller than 500 MB.");
      $("video-file").value = "";
      return;
    }
    busyState(true);
    message("source-message", null);
    message("upload-status", `Uploading ${file.name}…`);
    $("upload-progress").hidden = false;
    $("upload-progress").value = 0;
    try {
      const data = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/upload");
        xhr.timeout = 10 * 60 * 1000;
        xhr.upload.addEventListener("progress", (event) => {
          if (event.lengthComputable) $("upload-progress").value = event.loaded / event.total * 100;
        });
        xhr.upload.addEventListener("load", () => message("upload-status", "Checking video and preparing preview…"));
        xhr.addEventListener("load", () => {
          try {
            const result = JSON.parse(xhr.responseText);
            if (xhr.status >= 200 && xhr.status < 300) resolve(result);
            else reject(new Error(result.error || "Upload failed."));
          } catch (_) { reject(new Error("The upload server did not return a valid response.")); }
        });
        xhr.addEventListener("error", () => reject(new Error("Upload interrupted. Check the local server and try again.")));
        xhr.addEventListener("timeout", () => reject(new Error("The upload timed out. Try a smaller video.")));
        const form = new FormData(); form.append("video", file); xhr.send(form);
      });
      await activate(data.source.id);
      await library();
      message("source-message", data.source.zones.length ? "Video opened with its saved parking zones." : "Video uploaded. Mark its parking spaces to start detection.");
      await openEditor(data.source.id);
    } catch (error) { message("source-message", error.message); }
    finally {
      busyState(false);
      $("upload-progress").hidden = true;
      message("upload-status", null);
      $("video-file").value = "";
    }
  });

  function remember() { history.push({zones: clone(zones), selected}); if (history.length > 50) history.shift(); }
  function valid(polygon) {
    const turns = polygon.map((a, i) => {
      const b = polygon[(i + 1) % 4], c = polygon[(i + 2) % 4];
      return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
    });
    const area = Math.abs(polygon.reduce((sum, a, i) => {const b = polygon[(i + 1) % 4]; return sum + a[0] * b[1] - a[1] * b[0];}, 0)) / 2;
    return (turns.every(v => v > 0) || turns.every(v => v < 0)) && area >= 0.00001;
  }
  function svg(tag, attrs, text) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value)));
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function draw() {
    if (!record) return;
    const w = record.width, h = record.height;
    canvas.setAttribute("viewBox", `0 0 ${w} ${h}`);
    canvas.replaceChildren();
    zones.forEach((zone, index) => {
      canvas.append(svg("polygon", {points: zone.map(p => `${p[0] * w},${p[1] * h}`).join(" "), class: index === selected ? "zone selected" : "zone", "data-zone": index}));
      const center = zone.reduce((sum, p) => [sum[0] + p[0] / 4, sum[1] + p[1] / 4], [0, 0]);
      canvas.append(svg("text", {x: center[0] * w, y: center[1] * h, "font-size": w / 55, class: "zone-label", "text-anchor": "middle"}, index + 1));
    });
    // Handles stay above every polygon, including overlapping spaces.
    if (selected >= 0 && !drawing) zones[selected].forEach((point, corner) => canvas.append(svg("circle", {cx: point[0] * w, cy: point[1] * h, r: w / 90, class: "zone-handle", "data-corner": corner})));
    if (pending.length) {
      canvas.append(svg("polyline", {points: pending.map(p => `${p[0] * w},${p[1] * h}`).join(" "), class: "draft-zone"}));
      pending.forEach(p => canvas.append(svg("circle", {cx: p[0] * w, cy: p[1] * h, r: w / 110, class: "zone-handle"})));
    }
    canvas.classList.toggle("drawing", drawing);
    $("cancel-drawing").hidden = !drawing;
    $("draw-zone").disabled = saving || !imageReady || zones.length >= 200;
    $("add-rectangle").disabled = saving || !imageReady || zones.length >= 200;
    $("delete-zone").disabled = saving || selected < 0;
    $("undo-zone").disabled = saving || (!history.length && !pending.length);
    $("save-zones").disabled = saving || !imageReady || pending.length > 0;
    $("editor-zone-count").textContent = `${zones.length} parking ${zones.length === 1 ? "space" : "spaces"}`;
    $("editor-instructions").textContent = drawing ? `Click corner ${pending.length + 1} of 4, going around the parking space in order.` : "Select a space to move it or drag its corners. Draw a space to add four corners, or add a rectangle and adjust it.";
    $("zone-select").replaceChildren(new Option("None", "-1"), ...zones.map((_, i) => new Option(`Space ${i + 1}`, String(i))));
    $("zone-select").value = String(selected);
    $("corner-fields").disabled = selected < 0 || saving;
    if (selected >= 0) zones[selected].forEach((point, i) => point.forEach((value, axis) => {
      const input = $(`corner-${i}-${axis}`);
      input.value = (value * 100).toFixed(2);
    }));
  }

  async function openEditor(id) {
    record = await request(`/api/sources/${id}/zones`);
    zones = clone(record.zones); selected = -1; pending = []; drawing = false; history = []; imageReady = false;
    $("editor-source").textContent = record.name;
    $("smoothing-select").value = record.smoothing;
    message("editor-error", null);
    $("editor-image").onload = () => { imageReady = true; draw(); };
    $("editor-image").onerror = () => message("editor-error", "Preview could not load. Close the editor and try again.");
    $("editor-image").src = `/api/sources/${id}/preview?revision=${record.revision}`;
    draw();
    dialog.showModal();
  }
  $("edit-zones").addEventListener("click", async () => {
    busyState(true);
    try { await openEditor(activeId); } catch (error) { message("source-message", error.message); }
    finally { busyState(false); }
  });
  $("close-editor").addEventListener("click", () => dialog.close());
  dialog.addEventListener("cancel", (event) => { if (saving) event.preventDefault(); });
  $("draw-zone").addEventListener("click", () => { pending = []; drawing = true; selected = -1; message("editor-error", null); draw(); });
  $("cancel-drawing").addEventListener("click", () => { pending = []; drawing = false; draw(); });
  $("add-rectangle").addEventListener("click", () => {
    remember(); zones.push([[0.35,0.35],[0.65,0.35],[0.65,0.65],[0.35,0.65]]);
    selected = zones.length - 1; drawing = false; pending = []; draw();
  });
  $("zone-select").addEventListener("change", () => { selected = Number($("zone-select").value); drawing = false; pending = []; draw(); });
  $("delete-zone").addEventListener("click", () => { if (selected < 0) return; remember(); zones.splice(selected, 1); selected = -1; draw(); });
  $("undo-zone").addEventListener("click", () => {
    if (pending.length) pending.pop();
    else if (history.length) { const old = history.pop(); zones = old.zones; selected = old.selected; drawing = false; }
    message("editor-error", null); draw();
  });
  function point(event) {
    const rect = canvas.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))];
  }
  canvas.addEventListener("pointerdown", (event) => {
    if (!imageReady || saving || event.button !== 0) return;
    const p = point(event);
    if (drawing) {
      pending.push(p);
      if (pending.length === 4) {
        if (!valid(pending)) { pending.pop(); message("editor-error", "Corners must go around the space without crossing. Choose a different fourth corner."); }
        else { remember(); zones.push(pending); selected = zones.length - 1; pending = []; drawing = false; message("editor-error", null); }
      }
      draw(); return;
    }
    const corner = event.target.getAttribute("data-corner");
    const zone = event.target.getAttribute("data-zone");
    if (corner === null && zone === null) { selected = -1; draw(); return; }
    if (zone !== null) selected = Number(zone);
    remember();
    drag = {corner: corner === null ? null : Number(corner), start: p, original: clone(zones[selected])};
    canvas.setPointerCapture(event.pointerId);
    event.preventDefault(); draw();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const p = point(event);
    if (drag.corner !== null) zones[selected][drag.corner] = p;
    else {
      const xs = drag.original.map(p => p[0]), ys = drag.original.map(p => p[1]);
      const dx = Math.max(-Math.min(...xs), Math.min(1 - Math.max(...xs), p[0] - drag.start[0]));
      const dy = Math.max(-Math.min(...ys), Math.min(1 - Math.max(...ys), p[1] - drag.start[1]));
      zones[selected] = drag.original.map(p => [p[0] + dx, p[1] + dy]);
    }
    draw();
  });
  function finishDrag(event) {
    if (!drag) return;
    if (event.type === "pointercancel" || !valid(zones[selected])) {
      zones[selected] = drag.original; history.pop();
      message("editor-error", "Keep all four corners distinct and the space convex. The previous shape was restored.");
    }
    drag = null;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    draw();
  }
  canvas.addEventListener("pointerup", finishDrag);
  canvas.addEventListener("pointercancel", finishDrag);

  for (let i = 0; i < 4; i++) {
    const row = document.createElement("div"); row.className = "corner-row";
    const label = document.createElement("span"); label.textContent = `Corner ${i + 1}`; row.append(label);
    for (let axis = 0; axis < 2; axis++) {
      const field = document.createElement("label"); field.textContent = axis ? "Y " : "X ";
      const input = document.createElement("input"); input.type = "number"; input.min = "0"; input.max = "100"; input.step = "0.1"; input.id = `corner-${i}-${axis}`;
      input.setAttribute("aria-label", `Corner ${i + 1} ${axis ? "Y" : "X"} percent`);
      function commitCoordinate() {
        if (selected < 0) return;
        const candidate = clone(zones[selected]), value = input.valueAsNumber;
        candidate[i][axis] = value / 100;
        if (candidate[i][axis] === zones[selected][i][axis]) return;
        if (!Number.isFinite(value) || value < 0 || value > 100 || !valid(candidate)) {
          message("editor-error", "Coordinates must be between 0 and 100%, forming a non-crossing space.");
          input.value = (zones[selected][i][axis] * 100).toFixed(2); return;
        }
        remember(); zones[selected] = candidate; message("editor-error", null); draw();
      }
      input.addEventListener("change", commitCoordinate);
      input.addEventListener("blur", commitCoordinate);
      field.append(input); row.append(field);
    }
    $("corner-inputs").append(row);
  }
  $("save-zones").addEventListener("click", async () => {
    if (saving || !record || pending.length) return;
    saving = true; $("close-editor").disabled = true; draw();
    message("editor-error", null);
    try {
      await post(`/api/sources/${record.id}/zones`, {zones, revision: record.revision, smoothing: $("smoothing-select").value});
      dialog.close();
      await library();
      message("source-message", zones.length ? "Parking zones saved. Detection is restarting." : "Zones cleared. Add parking spaces to resume detection.");
      document.dispatchEvent(new Event("parking-refresh"));
    } catch (error) { message("editor-error", error.message); }
    finally { saving = false; $("close-editor").disabled = false; draw(); }
  });
  document.addEventListener("parking-status", (event) => {
    const id = event.detail.source_id;
    if (!busy && id && id !== activeId) library().catch(error => message("source-message", error.message));
  });
  library().catch(error => message("source-message", error.message));
})();
