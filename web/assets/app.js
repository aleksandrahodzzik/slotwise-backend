const emailInput = document.querySelector("#email");
const emailForm = document.querySelector("#email-form");
const sheet = document.querySelector("#sheet");
const strategyEl = document.querySelector("#strategy");
const ledeEl = document.querySelector("#lede");
const footEl = document.querySelector("#foot-strategy");

const state = {
  resources: [],
  resourceId: null,
  slots: [],
  mine: loadMine(),
  strategy: "",
  mode: "loading",
  notes: {},
  pending: null,
  message: "",
};

const copy = {
  row_lock: "Pick an hour. The API locks that slot’s row, then writes your seat. The next person in line hears that it is gone.",
  atomic_update: "Pick an hour. One conditional update claims a seat only while the count is still under capacity.",
  naive: "This desk is the broken demo. It reads the count, waits, and can sell the same seat twice.",
};

emailInput.value = localStorage.getItem("slotwise.email") || "";
emailInput.addEventListener("input", () => {
  localStorage.setItem("slotwise.email", emailInput.value.trim());
  emailForm.classList.remove("invalid");
});
emailForm.addEventListener("submit", (event) => event.preventDefault());
sheet.addEventListener("click", onSheetClick);

boot();

async function boot() {
  try {
    const health = await api("/health");
    state.strategy = health.body.strategy || "";
    await refresh();
    await reviveMine();
    render();
  } catch (error) {
    state.mode = "down";
    state.message = error.message || "The desk cannot see the API.";
    render();
  }
}

async function refresh() {
  const resources = await api("/api/v1/resources");
  state.resources = resources.body;
  if (!state.resources.length) {
    state.mode = "empty";
    state.slots = [];
    state.resourceId = null;
    return;
  }
  if (!state.resources.some((room) => room.id === state.resourceId)) {
    state.resourceId = state.resources[0].id;
  }
  const from = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const to = new Date(Date.now() + 14 * 24 * 60 * 60 * 1000).toISOString();
  const available = await api(
    `/api/v1/resources/${state.resourceId}/availability?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,
  );
  state.slots = available.body;
  state.mode = "ready";
}

async function reviveMine() {
  const rows = Object.values(state.mine);
  if (!rows.length) return;
  const kept = {};
  await Promise.all(
    rows.map(async (row) => {
      try {
        const found = await api(`/api/v1/bookings/${row.id}`);
        if (found.body.status === "confirmed") kept[found.body.id] = found.body;
      } catch {
        /* a wiped database forgets yesterday's seats */
      }
    }),
  );
  state.mine = kept;
  saveMine();
}

async function onSheetClick(event) {
  const demo = event.target.closest("[data-demo]");
  const room = event.target.closest("[data-room]");
  const book = event.target.closest("[data-book]");
  const cancel = event.target.closest("[data-cancel]");
  try {
    if (demo) return await openDemo();
    if (room) {
      state.resourceId = Number(room.dataset.room);
      state.notes = {};
      await refresh();
      render();
      return;
    }
    if (book) return await holdSeat(Number(book.dataset.book));
    if (cancel) return await releaseSeat(Number(cancel.dataset.cancel));
  } catch (error) {
    state.mode = "down";
    state.message = error.message || "The desk could not refresh.";
    render();
  }
}

async function openDemo() {
  state.mode = "creating";
  state.message = "Setting tomorrow’s hours in the North studio.";
  render();
  try {
    const created = await api("/api/v1/resources", {
      method: "POST",
      body: JSON.stringify({ name: "North studio", kind: "room" }),
    });
    const window = tomorrowWindow();
    await api(`/api/v1/resources/${created.body.id}/slots`, {
      method: "POST",
      body: JSON.stringify({
        from: window.from,
        to: window.to,
        duration_minutes: 30,
        capacity: 3,
      }),
    });
    state.resourceId = created.body.id;
    await refresh();
  } catch (error) {
    state.mode = "empty";
    state.message = error.message || "The demo desk could not be opened.";
  }
  render();
}

async function holdSeat(slotId) {
  if (state.pending || !emailInput.checkValidity()) {
    emailForm.classList.add("invalid");
    emailInput.focus();
    return;
  }
  const email = emailInput.value.trim();
  localStorage.setItem("slotwise.email", email);
  state.pending = slotId;
  delete state.notes[slotId];
  render();
  try {
    const result = await api("/api/v1/bookings", {
      method: "POST",
      headers: { "Idempotency-Key": newId() },
      body: JSON.stringify({ slot_id: slotId, customer_email: email }),
    });
    state.mine[result.body.id] = result.body;
    saveMine();
    if (result.status === 200) state.notes[slotId] = "That request was already on the books.";
    await refresh();
  } catch (error) {
    state.notes[slotId] = error.message || "That seat could not be held.";
    try {
      await refresh();
    } catch {
      /* keep the note even if the refetch fails */
    }
  } finally {
    state.pending = null;
    render();
  }
}

async function releaseSeat(bookingId) {
  if (state.pending) return;
  state.pending = `cancel-${bookingId}`;
  render();
  try {
    await api(`/api/v1/bookings/${bookingId}/cancel`, { method: "POST" });
    delete state.mine[bookingId];
    saveMine();
    await refresh();
  } catch (error) {
    const slotId = Object.values(state.mine).find((row) => row.id === bookingId)?.slot_id;
    if (slotId) state.notes[slotId] = error.message || "The seat could not be released.";
  } finally {
    state.pending = null;
    render();
  }
}

function render() {
  strategyEl.textContent = state.strategy ? strategyLabel(state.strategy) : "Desk offline";
  strategyEl.classList.toggle("warn", state.strategy === "naive");
  if (copy[state.strategy]) ledeEl.textContent = copy[state.strategy];
  footEl.textContent = state.strategy ? `Strategy on this process: ${state.strategy}` : "";
  sheet.className = state.mode === "ready" ? "sheet" : "sheet empty-sheet";
  sheet.innerHTML = sheetMarkup();
}

function sheetMarkup() {
  if (state.mode === "loading" || state.mode === "creating") {
    return `<div class="empty"><p class="eyebrow">One moment</p><h2>${escapeHtml(state.message || "Opening the book.")}</h2></div>`;
  }
  if (state.mode === "down") {
    return `<div class="empty"><p class="eyebrow">No line to Postgres</p><h2>The desk is quiet.</h2><p>${escapeHtml(state.message)}</p></div>`;
  }
  if (state.mode === "empty") {
    return `<div class="empty"><p class="eyebrow">Nothing on the book</p><h2>The desk is clear.</h2><p>${escapeHtml(state.message || "Open tomorrow in the North studio. Sixteen half hours, three seats in each.")}</p><button class="demo" type="button" data-demo>Open a demo desk</button></div>`;
  }
  const room = state.resources.find((item) => item.id === state.resourceId) || state.resources[0];
  const days = groupDays(state.slots);
  const capacity = state.slots[0] ? state.slots[0].capacity : null;
  return `<aside class="rail"><p class="index">Desk 01</p>${railRooms(room)}<p class="when-day">${escapeHtml(days[0]?.label || "No hours yet")}</p><p class="seat-line">${capacity ? `${capacity} seats in each hour` : "No hours in this window"}</p><p class="legend"><span><i class="pip taken"></i> Taken</span><span><i class="pip"></i> Open</span></p></aside><div class="hours">${hoursMarkup(days)}</div>`;
}

function railRooms(room) {
  if (state.resources.length < 2) {
    return `<h2 class="room-name">${escapeHtml(room.name)}</h2><p class="kind">${escapeHtml(room.kind)}</p>`;
  }
  return `<div class="rooms">${state.resources
    .map(
      (item) =>
        `<button type="button" class="room${item.id === state.resourceId ? " is-on" : ""}" data-room="${item.id}"><span>${escapeHtml(item.name)}</span><small>${escapeHtml(item.kind)}</small></button>`,
    )
    .join("")}</div>`;
}

function hoursMarkup(days) {
  if (!state.slots.length) return `<p class="quiet-note">No hours in the next two weeks.</p>`;
  const head = `<div class="colhead"><span>Hour</span><span>Seats</span><span></span></div>`;
  return (
    head +
    days
      .map((day, index) => {
        const banner = days.length > 1 ? `<p class="dayline">${escapeHtml(day.label)}</p>` : "";
        return banner + day.slots.map((slot, slotIndex) => slotMarkup(slot, index + slotIndex)).join("");
      })
      .join("")
  );
}

function slotMarkup(slot, index) {
  const mine = mineFor(slot.id);
  const full = slot.remaining <= 0 && !mine;
  const pending = state.pending === slot.id || (mine && state.pending === `cancel-${mine.id}`);
  const note = state.notes[slot.id] ? `<p class="note">${escapeHtml(state.notes[slot.id])}</p>` : "";
  const action = mine
    ? `<button class="book ghost" type="button" data-cancel="${mine.id}" ${pending ? "disabled" : ""}>${pending ? "Releasing…" : "Cancel"}</button>`
    : `<button class="book" type="button" data-book="${slot.id}" ${full || pending ? "disabled" : ""}>${pending ? "Holding…" : full ? "Full" : "Book"}</button>`;
  return `<article class="slot${mine ? " yours" : ""}${full ? " full" : ""}" style="animation-delay:${Math.min(index, 12) * 24}ms"><p class="when">${escapeHtml(formatTime(slot.starts_at))}–${escapeHtml(formatTime(slot.ends_at))}</p><div class="pips">${pips(slot)}<span class="remain">${slot.remaining} open</span>${mine ? '<span class="yours-tag">Yours</span>' : ""}</div>${action}${note}</article>`;
}

function pips(slot) {
  if (slot.capacity > 12) return "";
  const taken = Math.max(0, Math.min(slot.booked_count, slot.capacity));
  return Array.from({ length: slot.capacity }, (_, index) => `<i class="pip${index < taken ? " taken" : ""}" aria-hidden="true"></i>`).join("");
}

function mineFor(slotId) {
  return Object.values(state.mine).find((row) => row.slot_id === slotId && row.status === "confirmed");
}

function groupDays(slots) {
  const groups = [];
  for (const slot of slots) {
    const key = dayKey(slot.starts_at);
    const last = groups[groups.length - 1];
    if (!last || last.key !== key) groups.push({ key, label: dayLabel(slot.starts_at), slots: [slot] });
    else last.slots.push(slot);
  }
  return groups;
}

function strategyLabel(name) {
  if (name === "row_lock") return "Row lock";
  if (name === "atomic_update") return "Conditional update";
  if (name === "naive") return "Naive demo — do not use";
  return name;
}

function formatTime(iso) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Warsaw",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));
}

function dayLabel(iso) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Warsaw",
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(new Date(iso));
}

function dayKey(iso) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Warsaw",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(iso));
}

function tomorrowWindow() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Europe/Warsaw",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const pick = (type) => Number(parts.find((part) => part.type === type).value);
  const today = new Date(Date.UTC(pick("year"), pick("month") - 1, pick("day")));
  today.setUTCDate(today.getUTCDate() + 1);
  const year = today.getUTCFullYear();
  const month = today.getUTCMonth() + 1;
  const day = today.getUTCDate();
  return {
    from: zonedISO(year, month, day, 9, 0),
    to: zonedISO(year, month, day, 17, 0),
  };
}

function zonedISO(year, month, day, hour, minute) {
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  let instant = wall;
  for (let step = 0; step < 2; step += 1) {
    instant = wall - zoneOffsetMinutes(new Date(instant)) * 60 * 1000;
  }
  const offset = zoneOffsetMinutes(new Date(instant));
  const sign = offset >= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  const pad = (value) => String(value).padStart(2, "0");
  return `${year}-${pad(month)}-${pad(day)}T${pad(hour)}:${pad(minute)}:00${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

function zoneOffsetMinutes(date) {
  const formatted = new Intl.DateTimeFormat("en-US", {
    timeZone: "Europe/Warsaw",
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(date);
  const pick = (type) => Number(formatted.find((part) => part.type === type).value);
  const asUtc = Date.UTC(pick("year"), pick("month") - 1, pick("day"), pick("hour"), pick("minute"), pick("second"));
  return Math.round((asUtc - date.getTime()) / 60000);
}

function newId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const rand = Math.floor(Math.random() * 16);
    return (char === "x" ? rand : (rand & 0x3) | 0x8).toString(16);
  });
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers["content-type"] = "application/json";
  let response;
  try {
    response = await fetch(path, { ...options, headers, cache: "no-store" });
  } catch {
    throw new Error("The desk cannot reach the API.");
  }
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      throw new Error("The API returned something that is not JSON.");
    }
  }
  if (!response.ok) throw new Error(body?.detail || "The request was refused.");
  return { status: response.status, body };
}

function loadMine() {
  try {
    return JSON.parse(sessionStorage.getItem("slotwise.mine") || "{}");
  } catch {
    return {};
  }
}

function saveMine() {
  sessionStorage.setItem("slotwise.mine", JSON.stringify(state.mine));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}
