const $ = (selector) => document.querySelector(selector);
const state = { areas: [], sources: [], filter: "all", query: "" };
const api = async (url, options = {}) => {
  let response;
  try {
    response = await fetch(url, { ...options, cache: "no-store", headers: { "Content-Type": "application/json", ...options.headers } });
  } catch {
    throw new Error("Cannot reach the parking server. Run python server.py and open http://127.0.0.1:8000.");
  }
  if (response.status === 204) return null;
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error(`The parking API returned a web page (${response.status}). Restart python server.py, then open http://127.0.0.1:8000 instead of a static preview or Live Server.`);
  }
  let body;
  try { body = await response.json(); }
  catch { throw new Error("The parking server returned invalid data. Restart python server.py and refresh this page."); }
  if (!response.ok) throw new Error(body.error || "Something went wrong.");
  return body;
};

function toast(message, isError = false) {
  const box = $("#toast"); box.textContent = message; box.className = `visible ${isError ? "error" : ""}`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { box.className = ""; }, 3200);
}
function renderSummary(summary) {
  $("#total-capacity").textContent = summary.capacity; $("#total-occupied").textContent = summary.occupied;
  $("#total-available").textContent = summary.available; $("#hero-available").textContent = summary.available; $("#total-sessions").textContent = summary.activeSessions;
}
function filteredAreas() {
  return state.areas.filter((area) => {
    const matchesFilter = state.filter === "all" || area.status.toLowerCase() === state.filter;
    const matchesSearch = `${area.name} ${area.location}`.toLowerCase().includes(state.query);
    return matchesFilter && matchesSearch;
  });
}
function renderAreas() {
  const grid = $("#area-grid"); grid.innerHTML = "";
  const areas = filteredAreas(); const template = $("#area-template");
  if (!areas.length) { grid.innerHTML = '<p class="empty">No areas match this view. Try another filter or add a parking area.</p>'; return; }
  areas.forEach((area) => {
    const card = template.content.cloneNode(true);
    card.querySelector("h3").textContent = area.name; card.querySelector(".location").textContent = area.location || "Location not specified";
    const status = card.querySelector(".status"); status.textContent = area.status; status.classList.add(area.status === "Full" ? "full" : "available-status");
    card.querySelector(".available").textContent = area.available; card.querySelector(".capacity").textContent = area.capacity; card.querySelector(".occupied").textContent = area.occupied;
    card.querySelector(".progress span").style.width = `${area.occupancyPercent}%`;
    const entry = card.querySelector(".entry"); entry.disabled = area.available === 0; entry.addEventListener("click", () => updateOccupancy(area, 1));
    const exit = card.querySelector(".exit"); exit.disabled = area.occupied === 0; exit.addEventListener("click", () => updateOccupancy(area, -1));
    card.querySelector(".set-count").addEventListener("click", () => setCount(area));
    card.querySelector(".edit").addEventListener("click", () => openEditor(area));
    card.querySelector(".delete").addEventListener("click", () => deleteArea(area)); grid.append(card);
  });
}
function formatWhen(iso) {
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(new Date(iso));
}
function renderEvents(events) {
  const list = $("#event-list"); list.innerHTML = "";
  if (!events.length) { list.innerHTML = '<p class="empty">No occupancy changes have been recorded yet.</p>'; return; }
  events.forEach((event) => {
    const row = document.createElement("article"); row.className = "event";
    const direction = event.change > 0 ? "entry" : event.change < 0 ? "exit" : "adjust";
    row.innerHTML = `<span class="event-icon ${direction}">${event.change > 0 ? "+" : event.change < 0 ? "−" : "="}</span><div><strong>${event.areaName}</strong><p>${event.reason}</p></div><span class="event-count ${direction}">${event.change > 0 ? "+" : ""}${event.change} vehicle${Math.abs(event.change) === 1 ? "" : "s"}</span><time>${formatWhen(event.createdAt)}</time>`;
    list.append(row);
  });
}
function renderCameraSources() {
  const list = $("#camera-list"); const select = $("#camera-area"); list.innerHTML = "";
  const selected = select.value; select.innerHTML = '<option value="">Choose a parking area</option>';
  state.areas.forEach((area) => { const option = document.createElement("option"); option.value = area.id; option.textContent = `${area.name} (${area.available} available)`; select.append(option); });
  if (selected) select.value = selected;
  $("#camera-count").textContent = `${state.sources.length} source${state.sources.length === 1 ? "" : "s"}`;
  if (!state.sources.length) { list.innerHTML = '<p class="camera-empty">No sources yet. Add one to prepare a camera integration.</p>'; return; }
  const template = $("#camera-template");
  state.sources.forEach((source) => {
    const card = template.content.cloneNode(true); card.querySelector("strong").textContent = source.name;
    card.querySelector("p").textContent = source.areaName || "Area not assigned";
    const run = card.querySelector(".run-scan"); run.addEventListener("click", () => simulateScan(source)); list.append(card);
  });
}
function renderSessions(sessions) {
  const list = $("#session-list"); const select = $("#session-area"); list.innerHTML = "";
  const selected = select.value; select.innerHTML = '<option value="">Choose a parking area</option>';
  state.areas.filter((area) => area.available > 0).forEach((area) => { const option = document.createElement("option"); option.value = area.id; option.textContent = `${area.name} (${area.available} available)`; select.append(option); });
  if (selected) select.value = selected;
  $("#session-count").textContent = `${sessions.length} vehicle${sessions.length === 1 ? "" : "s"}`;
  if (!sessions.length) { list.innerHTML = '<p class="camera-empty">No active vehicle sessions.</p>'; return; }
  const template = $("#session-template");
  sessions.forEach((session) => { const card = template.content.cloneNode(true); card.querySelector(".plate").textContent = session.vehicleNumber; card.querySelector("strong").textContent = session.areaName; card.querySelector("p").textContent = session.vehicleType; card.querySelector("time").textContent = `In ${formatWhen(session.entryTime)}`; card.querySelector(".checkout").addEventListener("click", () => checkout(session)); list.append(card); });
}
async function load() {
  if ($("#spot-destination").value) refreshSpot();
  try { const [areas, summary, events, sources, sessions] = await Promise.all([api("/api/areas"), api("/api/summary"), api("/api/events"), api("/api/camera-sources"), api("/api/sessions")]); state.areas = areas.areas; state.sources = sources.sources; renderAreas(); renderSummary(summary); renderEvents(events.events); renderCameraSources(); renderSessions(sessions.sessions); }
  catch (error) { toast(error.message, true); }
}
async function simulateScan(source) {
  const area = state.areas.find((item) => item.id === source.areaId);
  const value = prompt(`Simulate detected occupancy for ${area?.name || source.name} (0 to ${area?.capacity ?? ""})`, area?.occupied ?? 0);
  if (value === null) return;
  try { await api("/api/scan-events", { method: "POST", body: JSON.stringify({ sourceId: source.id, detectedOccupied: value, confidence: 0.92 }) }); toast("Camera scan recorded."); await load(); } catch (error) { toast(error.message, true); }
}
async function updateOccupancy(area, change) {
  try { await api(`/api/areas/${area.id}/occupancy`, { method: "POST", body: JSON.stringify({ change, reason: change > 0 ? "Vehicle entry" : "Vehicle exit" }) }); await load(); }
  catch (error) { toast(error.message, true); }
}
async function setCount(area) {
  const value = prompt(`Set current occupancy for ${area.name} (0 to ${area.capacity})`, area.occupied);
  if (value === null) return;
  try { await api(`/api/areas/${area.id}/occupancy`, { method: "POST", body: JSON.stringify({ occupied: value, reason: "Manual count adjustment" }) }); toast("Occupancy updated."); await load(); }
  catch (error) { toast(error.message, true); }
}
function openEditor(area) {
  const form = $("#edit-area-form"); form.elements.id.value = area.id; form.elements.name.value = area.name; form.elements.location.value = area.location; form.elements.capacity.value = area.capacity; $("#area-dialog").showModal();
}
async function deleteArea(area) {
  if (!confirm(`Delete ${area.name}? This cannot be undone.`)) return;
  try { await api(`/api/areas/${area.id}`, { method: "DELETE" }); toast("Parking area deleted."); await load(); } catch (error) { toast(error.message, true); }
}
async function checkout(session) {
  try { await api(`/api/sessions/${session.id}/checkout`, { method: "POST", body: "{}" }); toast(`${session.vehicleNumber} checked out.`); await load(); } catch (error) { toast(error.message, true); }
}
$("#add-area-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const form = new FormData(event.currentTarget);
  try { await api("/api/areas", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) }); event.currentTarget.reset(); toast("Parking area added."); await load(); } catch (error) { toast(error.message, true); }
});
$("#camera-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const form = new FormData(event.currentTarget);
  try { await api("/api/camera-sources", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) }); event.currentTarget.reset(); toast("Camera source added."); await load(); } catch (error) { toast(error.message, true); }
});
$("#check-in-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const form = new FormData(event.currentTarget);
  try { await api("/api/sessions", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) }); event.currentTarget.reset(); toast("Vehicle checked in."); await load(); } catch (error) { toast(error.message, true); }
});
$("#edit-area-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const form = new FormData(event.currentTarget); const id = form.get("id"); form.delete("id");
  try { await api(`/api/areas/${id}`, { method: "PATCH", body: JSON.stringify(Object.fromEntries(form)) }); $("#area-dialog").close(); toast("Parking area updated."); await load(); } catch (error) { toast(error.message, true); }
});
$("#area-search").addEventListener("input", (event) => { state.query = event.target.value.trim().toLowerCase(); renderAreas(); });
document.querySelectorAll(".filter").forEach((button) => button.addEventListener("click", () => { state.filter = button.dataset.filter; document.querySelectorAll(".filter").forEach((item) => item.classList.toggle("active", item === button)); renderAreas(); }));
let spotRequest = 0;
async function refreshSpot() {
  const request = ++spotRequest;
  const result = $("#spot-result");
  result.textContent = "Checking current availability…";
  try {
    const data = await api(`/api/recommendations?destination=${encodeURIComponent($("#spot-destination").value)}&preference=${encodeURIComponent($("#spot-preference").value)}`);
    if (request !== spotRequest) return;
    result.replaceChildren();
    const text = (tag, content) => { const node = document.createElement(tag); node.textContent = content; result.append(node); return node; };
    if (!data.options.length) {
      text("h3", "No matching parking area available");
      text("p", "Try another destination or check the availability list below.");
    } else {
      const best = data.options[0];
      text("h3", `Park at ${best.name}`);
      text("p", `${best.available} spaces available · ${best.walkMinutes}-minute walk · ${best.pressure}`);
      text("p", data.reason);
      text("small", `Occupancy last recorded: ${formatWhen(best.updatedAt)}. Check availability on arrival.`);
      const button = text("button", "Use for vehicle check-in");
      button.type = "button";
      button.addEventListener("click", () => {
        $("#session-area").value = String(best.id);
        $("#vehicles").scrollIntoView({behavior:"smooth"});
        $("#check-in-form input").focus({preventScroll:true});
      });
      if (data.options.length > 1) {
        text("h4", "Other options");
        data.options.slice(1).forEach(item => text("p", `${item.name}: ${item.walkMinutes}-minute walk, ${item.available} spaces available. ${item.pressure}.`));
      }
    }
    if (data.unmappedAreas) text("p", `${data.unmappedAreas} available area(s) excluded because walking times are not configured.`);
    if (data.fullAreas) text("p", `${data.fullAreas} full area(s) excluded.`);
    $(".spot-note").textContent = `${data.demo ? "Demo campus walking times. " : "Configured walking-time estimates. "}This is a recommendation, not a reservation. Counts reflect recorded updates, not a forecast.`;
  } catch(error) { if (request === spotRequest) result.textContent = error.message; }
}
$("#spot-form").addEventListener("submit", event => { event.preventDefault(); refreshSpot(); });
$("#spot-destination").addEventListener("change", refreshSpot);
$("#spot-preference").addEventListener("change", () => { if ($("#spot-destination").value) refreshSpot(); });
async function loadDestinations() {
  try {
    const data = await api("/api/destinations");
    const select = $("#spot-destination"); select.replaceChildren();
    data.destinations.forEach(item => { const option = document.createElement("option"); option.value = item.id; option.textContent = item.name; select.append(option); });
    if (select.value) refreshSpot();
  } catch(error) { $("#spot-result").textContent = error.message; }
}
$("#refresh").addEventListener("click", load); load(); loadDestinations();
