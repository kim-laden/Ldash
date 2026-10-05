const KEY = "laden-ops-v1"

const KINDS = [
  ["apps", "Programs", "LAUNCH"],
  ["monitor", "Monitor", "LIVE"],
  ["calendar", "Calendar", "DATE"],
  ["sticky", "Sticky", "NOTE"],
  ["qdue", "Q-due", "TODO"],
  ["subscriptions", "Monthly subs", "MND"],
  ["weather", "Weather", "SKY"],
  ["vault", "Vault", "VAULT"],
]

const DEFAULT_CLASSES = [
  { id: "S", label: "S", span: 3 },
  { id: "M", label: "M", span: 4 },
  { id: "L", label: "L", span: 6 },
]

const MIN_BY_KIND = { apps: 3, monitor: 3, calendar: 4, sticky: 2, qdue: 3, subscriptions: 3, weather: 3, vault: 4 }

const SUB_CATEGORIES = [
  ["web", "Web"],
  ["ai", "AI"],
  ["music", "Music"],
  ["video", "Video"],
  ["other", "Other"],
]

const SUB_COLOR = { web: "#00e5ff", ai: "#00ff9d", music: "#a78bfa", video: "#ffd166", other: "#8b9bb4" }

const FIELD_OPTIONS = {
  app: ["cpu", "mem", "rss", "pid", "state", "threads", "count"],
  window: ["caption", "class", "active", "size", "pid", "minimized"],
  api: ["value"],
}

const state = load()
const hist = {}
let settingsOpen = false
let calCursor = new Date()
let selectedDay = iso(new Date())
let monitorTimer = 0
let dragState = null

const board = document.getElementById("board")
const settingsEl = document.getElementById("settings")
const clockEl = document.getElementById("clock")
const shortcutPill = document.getElementById("shortcut-pill")
const editBanner = document.getElementById("edit-banner")

document.documentElement.dataset.accent = state.settings.accent
if (shortcutPill) shortcutPill.textContent = state.settings.shortcut

document.getElementById("settings-btn").onclick = () => {
  settingsOpen = !settingsOpen
  renderSettings()
}
document.addEventListener("click", (event) => {
  const inside = event.target.closest ? event.target.closest(".drop") : null
  document.querySelectorAll(".drop.open").forEach((el) => {
    if (el !== inside && el._close) el._close()
  })
})
window.addEventListener("scroll", () => {
  document.querySelectorAll(".drop.open").forEach((el) => { if (el._place) el._place() })
}, true)
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.querySelectorAll(".drop.open").forEach((el) => { if (el._close) el._close() })
    if (settingsOpen) {
      settingsOpen = false
      renderSettings()
    }
  }
})

const setupUi = { step: "gate", email: "", password: "", confirm: "", username: "", city: "", picks: null, error: "", busy: false, localOnly: false, fresh: false, pendingRaw: "" }
let monitorsOn = false

renderOpening()
if (clockEl) {
  tickClock()
  setInterval(tickClock, 1000)
}
boot()

function load() {
  const base = defaultState()
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "null")
    if (!raw) return base
    const settings = { ...base.settings, ...(raw.settings || {}), sizeClasses: normalizeClasses(raw.settings?.sizeClasses) }
    settings.hostShow = { ...base.settings.hostShow, ...(raw.settings?.hostShow || {}) }
    settings.flipView = raw.settings?.flipView === "economy" ? "economy" : "journal"
    if (raw.settings?.setupDone == null) settings.setupDone = true
    const grid = Array.isArray(raw.grid) && raw.grid.length ? raw.grid.map((g) => ({ ...g })) : base.grid.map((g) => ({ ...g }))
    if (!grid.some((g) => g.kind === "vault")) {
      grid.push({ id: "vault", kind: "vault", size: "L", order: 0, visible: true })
      if (settings.capacity === 14 || settings.capacity === 24) settings.capacity = 36
    }
    if (!grid.some((g) => g.kind === "qdue")) {
      grid.push({ id: "qdue", kind: "qdue", size: "M", order: 5, visible: true })
    }
    if (!grid.some((g) => g.kind === "weather")) {
      grid.push({ id: "weather", kind: "weather", size: "S", order: 6, visible: true })
    }
    const classSpan = (id) => {
      const found = settings.sizeClasses.find((c) => c.id === id) || settings.sizeClasses[0]
      return clamp(found?.span, 1, 12, 4)
    }
    for (const widget of grid) {
      widget.span = clamp(widget.span, 1, 12, classSpan(widget.size))
      const fallback = Math.min(MIN_BY_KIND[widget.kind] || 2, widget.span)
      widget.minSpan = Math.min(clamp(widget.minSpan, 1, 12, fallback), widget.span)
    }
    return {
      ...base,
      ...raw,
      settings,
      grid,
      apps: raw.apps || base.apps,
      monitors: raw.monitors || [],
      events: raw.events || [],
      subscriptions: Array.isArray(raw.subscriptions) ? raw.subscriptions.map(normalizeSub) : [],
      economy: ensureEconomy(raw.economy),
      sticky: typeof raw.sticky === "string" ? raw.sticky : (raw.sticky?.text || ""),
      qdues: Array.isArray(raw.qdues) ? raw.qdues.map(normalizeQdue).filter((ticket) => ticket.title) : [],
      vaultFs: ensureVaultFs(raw.vaultFs, raw.vault),
      journal: ensureJournal(raw.journal),
    }
  } catch {
    return base
  }
}

function defaultState() {
  return {
    settings: {
      shortcut: "Ctrl+`,Ctrl+|",
      pauseMode: false,
      accent: "hack",
      showCalendar: true,
      showSticky: true,
      showVault: true,
      bSide: false,
      flipView: "journal",
      callsign: "laden",
      kitLabel: "personal ops kit",
      vaultMasterHash: "",
      aiProvider: "xai",
      aiApiKey: "",
      capacity: 36,
      editMode: false,
      monVisual: "spark",
      hostShow: { cpu: true, mem: true, load: true, temp: true, gpu: true, gpuTemp: true },
      sizeClasses: DEFAULT_CLASSES.map((c) => ({ ...c })),
      setupDone: false,
      accountEmail: "",
      accountBase: "https://laden.no/ldash",
      accountToken: "",
      confUrl: "",
      autoLogin: null,
      localOnly: false,
      weatherCity: "",
    },
    grid: [
      { id: "apps", kind: "apps", size: "L", order: 0, visible: true },
      { id: "monitor", kind: "monitor", size: "M", order: 1, visible: true },
      { id: "calendar", kind: "calendar", size: "M", order: 2, visible: true },
      { id: "sticky", kind: "sticky", size: "S", order: 3, visible: true },
      { id: "qdue", kind: "qdue", size: "M", order: 5, visible: true },
      { id: "weather", kind: "weather", size: "S", order: 6, visible: true },
      { id: "subscriptions", kind: "subscriptions", size: "L", order: 4, visible: true },
      { id: "vault", kind: "vault", size: "L", order: 0, visible: true },
    ],
    vaultFs: defaultVaultFs(),
    journal: defaultJournal(),
    economy: defaultEconomy(),
    apps: [
      { id: uid(), label: "Terminal", command: "wezterm || kitty || konsole", color: "#00e5ff" },
      { id: uid(), label: "Files", command: "xdg-open ~", color: "#ffd166" },
      { id: uid(), label: "Laden", command: "xdg-open https://laden.no", color: "#00ff9d" },
      { id: uid(), label: "Dolphin", command: "dolphin", color: "#a78bfa" },
    ],
    monitors: [],
    events: [],
    sticky: "",
    subscriptions: [],
    qdues: [],
  }
}

function save() {
  const copy = { ...state, settings: { ...state.settings } }
  localStorage.setItem(KEY, JSON.stringify(copy))
}

async function saveKitNow(button, note) {
  button.disabled = true
  save()
  const result = await persistKit()
  button.disabled = false
  if (result.local?.ok) {
    button.textContent = "Saved"
    const bits = ["Saved vault.conf on this device."]
    if (result.online?.ok && result.online.confUrl) bits.push("Cloud copy updated.")
    else if (result.online?.pending || result.online?.soft) bits.push("Cloud copy syncs in the background.")
    else if (result.online?.error) bits.push(result.online.error)
    note.textContent = bits.join(" ")
  } else {
    button.textContent = "Not saved"
    note.textContent = result.local?.error || "Could not write vault.conf."
  }
  setTimeout(() => { if (button.isConnected) button.textContent = "Save" }, 1600)
}

async function acceptAccount(auth, password) {
  sessionSecret = password
  if (auth.token) state.settings.accountToken = auth.token
  if (auth.confUrl) state.settings.confUrl = auth.confUrl
  state.settings.accountBase = accountBase()
  state.settings.vaultMasterHash = await hashSecret(password)
  state.settings.setupDone = true
  save()
  // Cloud pull/merge is background-only. Never block the board.
  if (auth.confUrl) void softPullCloudKit(auth, password)
  return { ok: true, loaded: false }
}

function shouldAutoEnter() {
  return launchRoute(state.settings, { exists: !!state._localVault, sealed: !!state._sealedPending }) === "board"
}

function launchRoute(settings, local) {
  const exists = !!(local && local.exists)
  const sealed = !!(local && local.sealed)
  if (!exists) return "gate"
  if (sealed) return "unlock"
  if (settings.autoLogin === true) return "board"
  if (settings.autoLogin === false) return "unlock"
  return "autologin"
}

function renderOpening() {
  const btn = document.getElementById("settings-btn")
  if (btn) btn.hidden = true
  if (!board) return
  board.replaceChildren(h("section", { class: "setup-screen" }, [
    h("p", { class: "mono muted" }, ["LDASH 1.0"]),
    h("h2", {}, ["Opening"]),
    h("p", { class: "hint" }, ["Reading the vault on this device."]),
  ]))
}

function enterApp() {
  setupUi.busy = false
  setupUi.password = ""
  setupUi.confirm = ""
  const btn = document.getElementById("settings-btn")
  if (btn) btn.hidden = false
  showDragCatch()
  renderBoard()
  syncChrome()
  startMonitors()
}

function afterAuthSuccess() {
  if (state.settings.autoLogin == null) {
    setupUi.step = "autologin"
    setupUi.busy = false
    setupUi.error = ""
    renderSetup()
    return
  }
  enterApp()
}

async function loginLocalOrCloud(email, password) {
  const mail = String(email || "").trim().toLowerCase()
  if (state._sealedPending && state._sealedLocal) {
    try {
      const payload = await resolveVaultConf(parseVaultConfText(state._sealedLocal), password)
      const innerMail = String(payload.settings?.accountEmail || "").trim().toLowerCase()
      if (innerMail && mail && innerMail !== mail) {
        return { ok: false, error: "This device already has a vault. Use that email." }
      }
      await finishConfImport(state._sealedLocal, password, true)
      if (confUi.status && confUi.status.kind === "err") {
        return { ok: false, error: confUi.status.text }
      }
      return { ok: true, local: true }
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : "Email or password was not accepted." }
    }
  }
  if (state._localVault) {
    const localMail = String(state.settings.accountEmail || "").trim().toLowerCase()
    if (localMail && mail && localMail !== mail) {
      return { ok: false, error: "This device already has a vault. Use that email." }
    }
    if (!state.settings.vaultMasterHash || !(await verifySecret(password, state.settings.vaultMasterHash))) {
      return { ok: false, error: "Email or password was not accepted." }
    }
    sessionSecret = password
    state.settings.setupDone = true
    save()
    if (!state.settings.localOnly) void softCloudSync(localMail || mail, password)
    return { ok: true, local: true }
  }
  if (state.settings.localOnly) return { ok: false, error: "No vault.conf on this device." }
  return loginFromCloud(mail, password)
}

async function loginFromCloud(email, password) {
  const auth = await accountAuth("login", email, password)
  if (!auth.ok) {
    if (auth.unreachable) return { ok: false, error: "Could not reach Laden. Import a vault.conf, or create the account on this device." }
    return { ok: false, error: auth.error || "Email or password was not accepted." }
  }
  let payload = null
  try {
    const pulled = await pullAccountConf(auth.confUrl, password)
    if (!pulled.ok) return { ok: false, error: pulled.error || "Could not read the cloud vault." }
    if (!pulled.empty && pulled.payload) payload = pulled.payload
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Could not open the cloud vault." }
  }
  if (!payload) {
    state.settings.accountEmail = email
    state.settings.accountToken = auth.token
    state.settings.confUrl = auth.confUrl
    state.settings.accountBase = accountBase()
    state.settings.vaultMasterHash = await hashSecret(password)
    sessionSecret = password
    save()
    return { ok: true, fresh: true }
  }
  applyVaultConf(payload, "replace", { quiet: true, preserveDevice: true })
  state.settings.accountEmail = state.settings.accountEmail || email
  state.settings.accountToken = auth.token
  state.settings.confUrl = auth.confUrl
  state.settings.accountBase = accountBase()
  if (!state.settings.vaultMasterHash) state.settings.vaultMasterHash = await hashSecret(password)
  state.settings.setupDone = true
  sessionSecret = password
  save()
  const stored = await persistKit()
  if (!stored.local?.ok) return { ok: false, error: stored.local?.error || "Could not write vault.conf." }
  state._localVault = true
  state._sealedPending = false
  return { ok: true, local: false, loaded: true }
}

async function softCloudSync(email, password) {
  if (state.settings.localOnly) return
  try {
    const auth = await accountAuth("login", email, password)
    if (!auth.ok) return
    state.settings.accountToken = auth.token
    state.settings.confUrl = auth.confUrl
    save()
    await pushAccountConf((await composePlainConf()).text)
  } catch (err) {
    // Fail-safe: never block the board on cloud sync.
  }
}

async function softCloudRegister(email, password) {
  if (state.settings.localOnly) return
  try {
    let auth = await accountAuth("register", email, password)
    if (!auth.ok) auth = await accountAuth("login", email, password)
    if (!auth.ok) return
    state.settings.accountToken = auth.token
    state.settings.confUrl = auth.confUrl
    save()
    await pushAccountConf((await composePlainConf()).text)
  } catch (err) {
    // Create already succeeded locally.
  }
}

async function softPullCloudKit(auth, password) {
  try {
    const pulled = await pullAccountConf(auth.confUrl, password)
    if (!pulled.ok || !pulled.payload) return
    applyVaultConf(pulled.payload, "replace", { preserveDevice: true })
    state.settings.accountToken = auth.token || state.settings.accountToken
    state.settings.confUrl = auth.confUrl
    save()
    await ladenCall("prepareUserFolder", { email: state.settings.accountEmail || "", username: state.settings.callsign || "" })
    await persistKit()
    if (state.settings.setupDone && shouldAutoEnter()) renderBoard()
  } catch (err) {
    // Optional merge. Board already open.
  }
}

function uid() {
  return crypto.randomUUID()
}

function normalizeClasses(raw) {
  const src = Array.isArray(raw) && raw.length ? raw : DEFAULT_CLASSES
  const seen = new Set()
  const out = []
  for (const item of src) {
    const id = String(item.id || "").replace(/[^\w-]/g, "").slice(0, 12)
    if (!id || seen.has(id)) continue
    seen.add(id)
    const span = clamp(item.span, 1, 12, 4)
    out.push({ id, label: String(item.label || id).slice(0, 16), span })
    if (out.length >= 8) break
  }
  return out.length ? out : DEFAULT_CLASSES.map((c) => ({ ...c }))
}

function clamp(value, min, max, fallback) {
  const n = Math.round(Number(value))
  if (!Number.isFinite(n)) return fallback
  return Math.max(min, Math.min(max, n))
}

function classById(id) {
  return state.settings.sizeClasses.find((c) => c.id === id) || state.settings.sizeClasses[0]
}

function panelSpan(widget) {
  const n = Number(widget.span)
  if (Number.isFinite(n) && n >= 1) return clamp(n, 1, 12, 4)
  return clamp(classById(widget.size).span, 1, 12, 4)
}

function panelMin(widget) {
  const pref = panelSpan(widget)
  const fallback = Math.min(MIN_BY_KIND[widget.kind] || 2, pref)
  const n = Number(widget.minSpan)
  const min = Number.isFinite(n) && n >= 1 ? clamp(n, 1, 12, fallback) : fallback
  return Math.min(min, pref)
}

function ownerName() {
  const raw = String(state.settings.callsign || vaultUi.localUser || "laden").trim() || "laden"
  return raw.charAt(0).toUpperCase() + raw.slice(1)
}

function vaultTitle() {
  return h("h2", { class: "vault-mark" }, ["Vault · ", h("em", {}, [ownerName()])])
}

function flipButton() {
  const on = !!state.settings.bSide
  return h("button", {
    class: "flip-btn" + (on ? " on" : ""),
    type: "button",
    title: on ? "Flip to the board" : "Flip to the journal",
    "aria-pressed": on ? "true" : "false",
    onclick: (event) => {
      event.preventDefault()
      event.stopPropagation()
      state.settings.bSide = !state.settings.bSide
      save()
      renderBoard()
      syncChrome()
    },
  }, ["⇄"])
}

function sideMark() {
  return h("div", { class: "brand-row" }, [flipButton(), vaultTitle()])
}

function setFlipView(view) {
  state.settings.flipView = view === "economy" ? "economy" : "journal"
  state.settings.bSide = true
  save()
  renderBoard()
  syncChrome()
}

function flipChoice() {
  const view = state.settings.flipView === "economy" ? "economy" : "journal"
  const opt = (id, label) => h("button", {
    class: "flip-opt" + (view === id ? " on" : ""),
    type: "button",
    "aria-pressed": view === id ? "true" : "false",
    onclick: (event) => {
      event.preventDefault()
      event.stopPropagation()
      setFlipView(id)
    },
  }, [label])
  return h("div", { class: "flip-choice", role: "group", "aria-label": "Flip side" }, [
    opt("journal", "Journal"),
    opt("economy", "Economy"),
  ])
}

function normalizeQdue(raw) {
  const checks = (Array.isArray(raw?.checks) ? raw.checks : []).map((check) => ({
    id: String(check?.id || uid()),
    text: String(check?.text || "").trim().slice(0, 160),
    done: !!check?.done,
  })).filter((check) => check.text).slice(0, 24)
  const done = !!raw?.done && checks.every((check) => check.done)
  return {
    id: String(raw?.id || uid()),
    title: String(raw?.title || "").trim().slice(0, 80),
    done,
    createdAt: raw?.createdAt || new Date().toISOString(),
    finishedAt: done ? (raw?.finishedAt || "") : "",
    checks,
  }
}

function qdueReady(ticket) {
  return (ticket.checks || []).every((check) => check.done)
}

function addQdue(title) {
  const name = String(title || "").trim().slice(0, 80)
  if (!name) return null
  const ticket = normalizeQdue({ id: uid(), title: name, checks: [], createdAt: new Date().toISOString() })
  state.qdues.push(ticket)
  save()
  renderBoard()
  return ticket
}

function addQdueCheck(ticketId, text) {
  const ticket = state.qdues.find((item) => item.id === ticketId)
  const line = String(text || "").trim().slice(0, 160)
  if (!ticket || ticket.done || !line || ticket.checks.length >= 24) return null
  const check = { id: uid(), text: line, done: false }
  ticket.checks.push(check)
  save()
  renderBoard()
  return check
}

function toggleQdueCheck(ticketId, checkId) {
  const ticket = state.qdues.find((item) => item.id === ticketId)
  const check = ticket?.checks.find((item) => item.id === checkId)
  if (!check) return
  check.done = !check.done
  if (!check.done && ticket.done) {
    ticket.done = false
    ticket.finishedAt = ""
  }
  save()
  renderBoard()
}

function finishQdue(ticketId) {
  const ticket = state.qdues.find((item) => item.id === ticketId)
  if (!ticket || !qdueReady(ticket)) return false
  ticket.done = true
  ticket.finishedAt = new Date().toISOString()
  save()
  renderBoard()
  return true
}

function reopenQdue(ticketId) {
  const ticket = state.qdues.find((item) => item.id === ticketId)
  if (!ticket) return
  ticket.done = false
  ticket.finishedAt = ""
  save()
  renderBoard()
}

function removeQdue(ticketId) {
  state.qdues = state.qdues.filter((item) => item.id !== ticketId)
  save()
  renderBoard()
}

function normalizeSub(sub) {
  const category = SUB_CATEGORIES.some(([id]) => id === sub.category) ? sub.category : "other"
  const cycle = sub.cycle === "yearly" || sub.cycle === "weekly" ? sub.cycle : "monthly"
  return {
    ...sub,
    name: String(sub.name || "Subscription").slice(0, 48),
    cost: Number(sub.cost) || 0,
    currency: String(sub.currency || "NOK").trim().toUpperCase().slice(0, 8) || "NOK",
    cycle,
    category,
    notes: String(sub.notes || ""),
    nextBilling: sub.nextBilling || "",
    color: sub.color || SUB_COLOR[category],
  }
}

function fitSummary() {
  const items = visibleItems()
  const want = items.reduce((sum, widget) => sum + panelSpan(widget), 0)
  const total = Number(state.settings.capacity || 36)
  if (!items.length) return "Every panel is hidden, so the total is free."
  if (want <= total) return "Shown panels use " + want + " of " + total + ". They fit."
  const fitted = withEffective(items).reduce((sum, widget) => sum + widget.effectiveSpan, 0)
  return "Shown panels want " + want + " of " + total + ". They shrink to " + fitted + " so they fit."
}

function visibleItems() {
  return state.grid
    .filter((g) => g.visible)
    .filter((g) => (g.kind === "calendar" ? state.settings.showCalendar : true))
    .filter((g) => (g.kind === "sticky" ? state.settings.showSticky : true))
    .filter((g) => (g.kind === "vault" ? state.settings.showVault !== false : true))
    .sort((a, b) => a.order - b.order)
}

function withEffective(items) {
  const budget = Number(state.settings.capacity || 36)
  const work = items.map((g) => ({ ...g, effectiveSpan: panelSpan(g) }))
  const total = () => work.reduce((sum, g) => sum + g.effectiveSpan, 0)
  let guard = 0
  while (total() > budget && guard < 80) {
    const candidates = work
      .map((g, i) => ({ i, slack: g.effectiveSpan - panelMin(g) }))
      .filter((c) => c.slack > 0)
      .sort((a, b) => b.slack - a.slack || b.i - a.i)
    if (!candidates.length) break
    work[candidates[0].i].effectiveSpan -= 1
    guard += 1
  }
  while (total() > budget && guard < 120) {
    let idx = -1
    let best = 1
    work.forEach((g, i) => {
      if (g.effectiveSpan > best) {
        best = g.effectiveSpan
        idx = i
      }
    })
    if (idx < 0) break
    work[idx].effectiveSpan -= 1
    guard += 1
  }
  return work
}

function balanceSpans(prefs) {
  const sum = prefs.reduce((a, b) => a + b, 0) || 1
  const exact = prefs.map((p) => (p / sum) * 12)
  const spans = exact.map((n) => Math.max(1, Math.floor(n)))
  let left = 12 - spans.reduce((a, b) => a + b, 0)
  const order = exact
    .map((n, i) => ({ i, frac: n - Math.floor(n) }))
    .sort((a, b) => b.frac - a.frac || a.i - b.i)
  let k = 0
  while (left > 0 && k < spans.length * 4) {
    spans[order[k % order.length].i] += 1
    left -= 1
    k += 1
  }
  k = 0
  while (left < 0 && k < spans.length * 4) {
    const idx = order[k % order.length].i
    if (spans[idx] > 1) {
      spans[idx] -= 1
      left += 1
    }
    k += 1
  }
  return spans
}

function pack(items) {
  const groups = []
  let row = []
  let used = 0
  for (const item of items) {
    const pref = Math.max(1, Math.min(12, item.effectiveSpan || panelSpan(item)))
    if (row.length && used + pref > 12) {
      groups.push(row)
      row = []
      used = 0
    }
    row.push({ item, pref })
    used += pref
  }
  if (row.length) groups.push(row)
  const out = []
  groups.forEach((group, rowIndex) => {
    const spans = balanceSpans(group.map((entry) => entry.pref))
    let col = 1
    group.forEach((entry, idx) => {
      const span = spans[idx]
      out.push({ ...entry.item, span, row: rowIndex + 1, col })
      col += span
    })
  })
  return out
}

function renderBoard() {
  syncChrome()
  if (dragState?.active) {
    dragState.stale = true
    return
  }
  if (state.settings.bSide) {
    board.classList.add("journal-board")
    board.style.gridTemplateRows = ""
    board.style.overflow = ""
    board.dataset.rows = "0"
    board.replaceChildren(state.settings.flipView === "economy" ? renderEconomy() : renderJournal())
    bindFrame()
    return
  }
  board.classList.remove("journal-board")
  const laid = pack(withEffective(visibleItems()))
  board.replaceChildren()
  if (!laid.length) {
    board.dataset.rows = "0"
    board.style.gridTemplateRows = ""
    board.append(h("p", { class: "empty" }, ["Every panel is hidden. Open Control to bring one back."]))
    bindFrame()
    return
  }
  for (const item of laid) board.append(renderWidget(item))
  layoutWidgets(false)
  paintMonitor()
  bindFrame()
}

function phoneBoard() {
  return window.matchMedia("(max-width: 720px)").matches
}

function shortcutHint() {
  if (state._shortcutBackend === "hotkey") {
    return "Global hotkey while Laden Ops is running. Default Ctrl+` (English) and Ctrl+| (Norwegian) both hide and show the window."
  }
  if (state._shortcutBackend === "iphone") {
    return "iPhone has no global hotkey. Leave the app with the Home gesture. The shortcut field is kept for your other computers."
  }
  if (state._shortcutBackend === "android") {
    return "Android has no global hotkey. Leave the app with the Home gesture. The shortcut field is kept for your other computers."
  }
  return "KDE global shortcut. Default Ctrl+` hides and shows this window."
}

function placeStyle(item) {
  if (phoneBoard()) return "grid-column: 1 / -1; grid-row: auto"
  return `grid-column: ${item.col} / span ${item.span}; grid-row: ${item.row}`
}

function renderWidget(item) {
  const meta = KINDS.find((k) => k[0] === item.kind) || [item.kind, item.kind, ""]
  const section = h("section", {
    class: "widget" + (state.settings.editMode ? " edit-mode" : ""),
    "data-id": item.id,
    style: placeStyle(item),
  })
  const headKids = item.kind === "vault"
    ? [sideMark()]
    : [
        h("h2", {}, [meta[1]]),
        h("span", { class: "tag" }, [meta[2] + " · " + (item.effectiveSpan || panelSpan(item))]),
      ]
  if (item.kind === "vault" && state.settings.editMode) {
    headKids.push(h("span", { class: "tag" }, [String(item.effectiveSpan || panelSpan(item))]))
  }
  section.append(h("div", { class: "widget-head" }, headKids))
  const body = h("div", { class: "widget-body", id: item.kind === "monitor" ? "monitor-body" : "" })
  if (item.kind === "apps") body.append(renderApps())
  if (item.kind === "calendar") body.append(renderCalendar())
  if (item.kind === "sticky") body.append(renderSticky())
  if (item.kind === "qdue") body.append(renderQdue())
  if (item.kind === "weather") body.append(renderWeather())
  if (item.kind === "subscriptions") body.append(renderSubs())
  if (item.kind === "vault") body.append(renderVault())
  if (item.kind === "monitor") body.append(h("p", { class: "empty" }, ["Sampling…"]))
  section.append(body)
  return section
}

function bindFrame() {
  if (board._frame) return
  board._frame = new ResizeObserver(() => fitRows())
  board._frame.observe(board)
  board.addEventListener("pointerdown", onBoardPointerDown)
  board._phone = window.matchMedia("(max-width: 720px)")
  board._phone.addEventListener("change", () => {
    if (board.classList.contains("journal-board")) return
    layoutWidgets(false)
  })
}

function fitRows() {
  if (board.classList.contains("journal-board")) return
  if (phoneBoard()) {
    board.style.overflow = "auto"
    board.style.gridTemplateRows = ""
    return
  }
  const rows = Number(board.dataset.rows || 0)
  if (!rows || board.clientHeight < 40) return
  const cs = getComputedStyle(board)
  const gap = parseFloat(cs.rowGap) || 12
  const inner = board.clientHeight - (parseFloat(cs.paddingTop) || 0) - (parseFloat(cs.paddingBottom) || 0)
  const minRow = 156
  const need = rows * minRow + gap * Math.max(0, rows - 1)
  if (inner >= need) {
    board.style.overflow = "hidden"
    board.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`
  } else {
    board.style.overflow = "auto"
    board.style.gridTemplateRows = `repeat(${rows}, ${minRow}px)`
  }
}

function layoutWidgets(animate) {
  const laid = pack(withEffective(visibleItems()))
  const byId = new Map(laid.map((item) => [item.id, item]))
  const prev = new Map()
  if (animate) {
    board.querySelectorAll(".widget:not(.lifted)").forEach((el) => {
      prev.set(el.dataset.id, el.getBoundingClientRect())
    })
  }
  let slot = board.querySelector(".widget-slot")
  const dragging = !!(dragState && dragState.active)
  if (dragging) {
    if (!slot) {
      slot = document.createElement("div")
      slot.className = "widget-slot"
      slot.setAttribute("aria-hidden", "true")
      board.append(slot)
    }
  } else if (slot) {
    slot.remove()
    slot = null
  }
  const rows = laid.reduce((max, item) => Math.max(max, item.row), 0)
  board.querySelectorAll(".widget").forEach((el) => {
    const item = byId.get(el.dataset.id)
    if (!item) return
    const column = phoneBoard() ? "1 / -1" : item.col + " / span " + item.span
    const row = phoneBoard() ? "auto" : String(item.row)
    if (el.classList.contains("lifted")) {
      if (slot) {
        slot.style.gridColumn = column
        slot.style.gridRow = row
      }
      return
    }
    el.style.gridColumn = column
    el.style.gridRow = row
  })
  board.dataset.rows = String(rows)
  fitRows()
  if (!animate) return
  board.querySelectorAll(".widget:not(.lifted)").forEach((el) => {
    const old = prev.get(el.dataset.id)
    if (!old) return
    const now = el.getBoundingClientRect()
    const dx = old.left - now.left
    const dy = old.top - now.top
    const sx = now.width ? old.width / now.width : 1
    const sy = now.height ? old.height / now.height : 1
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) return
    el._flip = (el._flip || 0) + 1
    const token = el._flip
    el.style.transition = "none"
    el.style.transformOrigin = "top left"
    el.style.transform = "translate(" + dx + "px, " + dy + "px) scale(" + sx + ", " + sy + ")"
    void el.offsetWidth
    requestAnimationFrame(() => {
      if (el._flip !== token) return
      el.style.transition = "transform 180ms cubic-bezier(.2, .8, .2, 1)"
      el.style.transform = ""
    })
  })
}

function writeOrder(ids) {
  ids.forEach((id, order) => {
    const widget = state.grid.find((g) => g.id === id)
    if (widget) widget.order = order
  })
}

function layoutRect(el) {
  const origin = boardOrigin()
  const left = origin.x + el.offsetLeft - board.scrollLeft
  const top = origin.y + el.offsetTop - board.scrollTop
  return { left, top, width: el.offsetWidth, height: el.offsetHeight, bottom: top + el.offsetHeight }
}

function indexUnder(x, y) {
  const nodes = [...board.querySelectorAll(".widget:not(.lifted)")]
  nodes.sort((a, b) => {
    const dy = a.offsetTop - b.offsetTop
    if (Math.abs(dy) > 8) return dy
    return a.offsetLeft - b.offsetLeft
  })
  for (let i = 0; i < nodes.length; i++) {
    const rect = layoutRect(nodes[i])
    if (y < rect.top) return i
    if (y <= rect.bottom && x < rect.left + rect.width / 2) return i
  }
  return nodes.length
}

function pushTo(fromId, index) {
  const ids = visibleItems().map((g) => g.id)
  const from = ids.indexOf(fromId)
  if (from < 0) return
  const rest = ids.filter((id) => id !== fromId)
  const dest = Math.max(0, Math.min(rest.length, index))
  if (dest === from) return
  rest.splice(dest, 0, fromId)
  writeOrder(rest)
  layoutWidgets(true)
}

function boardOrigin() {
  const rect = board.getBoundingClientRect()
  const cs = getComputedStyle(board)
  return {
    x: rect.left + (parseFloat(cs.borderLeftWidth) || 0),
    y: rect.top + (parseFloat(cs.borderTopWidth) || 0),
  }
}

function placeLifted(clientX, clientY) {
  if (!dragState?.el) return
  const origin = boardOrigin()
  dragState.el.style.left = (clientX - dragState.dx - origin.x + board.scrollLeft) + "px"
  dragState.el.style.top = (clientY - dragState.dy - origin.y + board.scrollTop) + "px"
}

function onBoardPointerDown(event) {
  if (!state.settings.editMode || state.settings.bSide || dragState) return
  if (event.button !== 0) return
  if (event.target.closest && event.target.closest("button, a, input, textarea, .drop")) return
  const head = event.target.closest ? event.target.closest(".widget-head") : null
  if (!head || !board.contains(head)) return
  const section = head.closest(".widget")
  if (!section?.dataset.id) return
  event.preventDefault()
  const rect = section.getBoundingClientRect()
  dragState = {
    id: section.dataset.id,
    el: section,
    pointerId: event.pointerId,
    dx: event.clientX - rect.left,
    dy: event.clientY - rect.top,
    startX: event.clientX,
    startY: event.clientY,
    active: false,
    stale: false,
  }
  section.setPointerCapture(event.pointerId)
  section.addEventListener("pointermove", onDragMove)
  section.addEventListener("pointerup", onDragEnd)
  section.addEventListener("pointercancel", onDragEnd)
}

function onDragMove(event) {
  if (!dragState || event.pointerId !== dragState.pointerId) return
  if (!dragState.active) {
    if (Math.hypot(event.clientX - dragState.startX, event.clientY - dragState.startY) < 5) return
    const el = dragState.el
    const rect = el.getBoundingClientRect()
    dragState.dx = event.clientX - rect.left
    dragState.dy = event.clientY - rect.top
    dragState.active = true
    el.classList.add("lifted")
    el.style.width = rect.width + "px"
    el.style.height = rect.height + "px"
    document.body.classList.add("is-pushing")
    placeLifted(event.clientX, event.clientY)
    layoutWidgets(false)
  }
  placeLifted(event.clientX, event.clientY)
  pushTo(dragState.id, indexUnder(event.clientX, event.clientY))
}

function onDragEnd(event) {
  if (!dragState || event.pointerId !== dragState.pointerId) return
  const section = dragState.el
  section.removeEventListener("pointermove", onDragMove)
  section.removeEventListener("pointerup", onDragEnd)
  section.removeEventListener("pointercancel", onDragEnd)
  const active = dragState.active
  const stale = dragState.stale
  const from = active ? section.getBoundingClientRect() : null
  section.classList.remove("lifted")
  section.style.width = ""
  section.style.height = ""
  section.style.left = ""
  section.style.top = ""
  document.body.classList.remove("is-pushing")
  dragState = null
  if (!active) return
  save()
  if (stale) {
    renderBoard()
    return
  }
  layoutWidgets(false)
  if (!from) return
  const now = section.getBoundingClientRect()
  const dx = from.left - now.left
  const dy = from.top - now.top
  const sx = now.width ? from.width / now.width : 1
  const sy = now.height ? from.height / now.height : 1
  if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) return
  section.style.transition = "none"
  section.style.transformOrigin = "top left"
  section.style.transform = "translate(" + dx + "px, " + dy + "px) scale(" + sx + ", " + sy + ")"
  void section.offsetWidth
  requestAnimationFrame(() => {
    section.style.transition = "transform 180ms cubic-bezier(.2, .8, .2, 1)"
    section.style.transform = ""
  })
}

function renderApps() {
  const wrap = h("div", { class: "apps" })
  for (const app of state.apps) {
    const btn = h("button", { class: "app-btn", type: "button", style: `--app:${app.color || "var(--green)"}` }, [
      h("strong", {}, [app.label]),
      h("span", { class: "mono" }, [app.command.slice(0, 42)]),
    ])
    btn.onclick = () => ladenCall("launch", { command: app.command })
    wrap.append(btn)
  }
  if (!state.apps.length) wrap.append(h("p", { class: "empty" }, ["Add a program in Control."]))
  return wrap
}

function renderCalendar() {
  const year = calCursor.getFullYear()
  const month = calCursor.getMonth()
  const cells = monthCells(year, month)
  const wrap = h("div", {})
  wrap.append(
    h("div", { class: "cal-head" }, [
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => shiftMonth(-1) }, ["‹"]),
      h("strong", {}, [calCursor.toLocaleString(undefined, { month: "long", year: "numeric" })]),
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => shiftMonth(1) }, ["›"]),
    ]),
  )
  const grid = h("div", { class: "cal-grid" })
  for (const name of ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]) grid.append(h("div", { class: "dow" }, [name]))
  for (const day of cells) {
    const key = iso(day)
    const inMonth = day.getMonth() === month
    const has = state.events.some((ev) => ev.date === key)
    const btn = h("button", {
      type: "button",
      class: "day" + (inMonth ? "" : " out") + (has ? " has" : "") + (key === selectedDay ? " on" : ""),
      onclick: () => {
        selectedDay = key
        renderBoard()
      },
    }, [String(day.getDate())])
    grid.append(btn)
  }
  wrap.append(grid)
  const todays = state.events.filter((ev) => ev.date === selectedDay)
  const list = h("div", {})
  list.append(h("p", { class: "muted" }, [selectedDay]))
  if (!todays.length) list.append(h("p", { class: "empty" }, ["Nothing booked."]))
  for (const ev of todays) {
    const row = h("div", { class: "event" }, [
      h("strong", {}, [(ev.time ? ev.time + " · " : "") + ev.title]),
      h("button", { class: "btn btn-danger", type: "button", onclick: () => removeEvent(ev.id) }, ["×"]),
    ])
    if (ev.notes) row.append(h("p", {}, [ev.notes]))
    list.append(row)
  }
  const title = h("input", { placeholder: "Appointment", id: "ev-title" })
  const time = h("input", { placeholder: "14:00", id: "ev-time" })
  const notes = h("input", { placeholder: "Notes", id: "ev-notes" })
  const add = h("button", { class: "btn btn-primary", type: "button" }, ["Add to " + selectedDay])
  add.onclick = () => {
    if (!title.value.trim()) return
    state.events.push({
      id: uid(),
      title: title.value.trim(),
      date: selectedDay,
      time: time.value.trim(),
      notes: notes.value.trim(),
    })
    save()
    renderBoard()
  }
  wrap.append(list, h("div", { class: "form" }, [h("div", { class: "inline" }, [title, time]), notes, add]))
  return wrap
}

function shiftMonth(delta) {
  calCursor = new Date(calCursor.getFullYear(), calCursor.getMonth() + delta, 1)
  renderBoard()
}

function removeEvent(id) {
  state.events = state.events.filter((ev) => ev.id !== id)
  save()
  renderBoard()
}

function renderSticky() {
  const box = h("textarea", { class: "sticky", placeholder: "Quick note…" }, [])
  box.value = state.sticky
  box.addEventListener("input", () => {
    state.sticky = box.value
    save()
  })
  return box
}

function renderQdue() {
  const wrap = h("div", { class: "qdue-board" })
  const title = h("input", { placeholder: "New ticket" })
  const add = h("button", { class: "btn btn-primary", type: "button" }, ["Add"])
  add.onclick = () => addQdue(title.value)
  title.addEventListener("keydown", (event) => {
    if (event.key === "Enter") addQdue(title.value)
  })
  wrap.append(h("div", { class: "qdue-compose" }, [title, add]))
  const open = state.qdues.filter((ticket) => !ticket.done)
  const closed = state.qdues.filter((ticket) => ticket.done)
  wrap.append(h("p", { class: "hint mono" }, [
    open.length + " open" + (closed.length ? " · " + closed.length + " written off" : ""),
  ]))
  if (!state.qdues.length) {
    wrap.append(h("p", { class: "empty" }, ["Queue is clear. Add a ticket. Lines on a ticket have to be crossed off before it can be written off."]))
    return wrap
  }
  for (const ticket of open) wrap.append(renderQdueTicket(ticket))
  for (const ticket of closed) wrap.append(renderQdueClosed(ticket))
  return wrap
}

function renderQdueTicket(ticket) {
  const ready = qdueReady(ticket)
  const crossed = ticket.checks.filter((check) => check.done).length
  const box = h("article", { class: "qdue" })
  box.append(h("div", { class: "qdue-top" }, [
    h("h3", {}, [ticket.title]),
    h("span", { class: "mono muted" }, [ticket.checks.length ? crossed + "/" + ticket.checks.length : "no lines"]),
    h("button", { class: "btn btn-danger", type: "button", title: "Delete ticket", onclick: () => removeQdue(ticket.id) }, ["×"]),
  ]))
  for (const check of ticket.checks) box.append(renderQdueCheck(ticket, check))
  const line = h("input", { placeholder: "Line to cross off" })
  const addLine = h("button", { class: "btn btn-ghost", type: "button" }, ["Add"])
  addLine.onclick = () => addQdueCheck(ticket.id, line.value)
  line.addEventListener("keydown", (event) => {
    if (event.key === "Enter") addQdueCheck(ticket.id, line.value)
  })
  const writeOff = h("button", {
    class: "btn " + (ready ? "btn-primary" : "btn-ghost"),
    type: "button",
    disabled: ready ? null : "true",
    onclick: () => finishQdue(ticket.id),
  }, ["Write off"])
  const note = !ticket.checks.length
    ? "No lines yet. Write it off, or add something that has to be crossed first."
    : ready
      ? "Every line is crossed. Write the ticket off."
      : "Cross every line before this ticket can be written off."
  box.append(
    h("div", { class: "qdue-compose" }, [line, addLine]),
    h("div", { class: "qdue-foot" }, [writeOff, h("p", { class: "hint" }, [note])]),
  )
  return box
}

function renderQdueCheck(ticket, check) {
  return h("div", { class: "qcheck" + (check.done ? " on" : "") }, [
    h("button", {
      class: "qbox",
      type: "button",
      title: check.done ? "Open this line" : "Cross off",
      onclick: () => toggleQdueCheck(ticket.id, check.id),
    }, [check.done ? "✓" : ""]),
    h("button", {
      class: "qlabel",
      type: "button",
      onclick: () => toggleQdueCheck(ticket.id, check.id),
    }, [check.text]),
    h("button", {
      class: "btn btn-danger",
      type: "button",
      title: "Remove line",
      onclick: () => {
        ticket.checks = ticket.checks.filter((item) => item.id !== check.id)
        if (ticket.done && !qdueReady(ticket)) {
          ticket.done = false
          ticket.finishedAt = ""
        }
        save()
        renderBoard()
      },
    }, ["×"]),
  ])
}

function renderQdueClosed(ticket) {
  return h("article", { class: "qdue done" }, [
    h("div", { class: "qdue-top" }, [
      h("h3", {}, [ticket.title]),
      h("span", { class: "mono muted" }, ["written off"]),
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => reopenQdue(ticket.id) }, ["Reopen"]),
      h("button", { class: "btn btn-danger", type: "button", title: "Delete ticket", onclick: () => removeQdue(ticket.id) }, ["×"]),
    ]),
  ])
}

function renderSubs() {
  const wrap = h("div", { class: "subs" })
  if (!state.subscriptions.length) {
    wrap.append(h("p", { class: "empty" }, ["Monthly subs live here. Add Spotify, YouTube Premium, web apps, AI — each line is the cost per month."]))
    return wrap
  }
  const totals = new Map()
  for (const sub of state.subscriptions) {
    const cur = (sub.currency || "NOK").toUpperCase()
    totals.set(cur, (totals.get(cur) || 0) + toMonthly(sub))
  }
  for (const [cur, total] of totals) {
    wrap.append(h("div", { class: "total sub-total" }, [
      h("span", {}, ["Per month"]),
      h("span", { class: "mono" }, [formatMoney(total, cur)]),
    ]))
  }
  for (const sub of state.subscriptions) {
    const monthly = toMonthly(sub)
    const cat = SUB_CATEGORIES.find(([id]) => id === sub.category)
    const cycleNote = sub.cycle && sub.cycle !== "monthly"
      ? sub.cycle + " " + formatMoney(sub.cost, sub.currency)
      : "per month"
    const extra = [cycleNote, sub.nextBilling ? "next " + sub.nextBilling : "", sub.notes || ""].filter(Boolean).join(" · ")
    wrap.append(h("article", { class: "sub" }, [
      h("div", { class: "bar", style: `background:${sub.color || SUB_COLOR[sub.category] || "var(--cyan)"}` }),
      h("div", {}, [
        h("h3", {}, [sub.name]),
        h("div", { class: "muted" }, [(cat ? cat[1] : "Other") + " · " + extra]),
      ]),
      h("div", { class: "cost" }, [formatMoney(monthly, sub.currency), h("span", { class: "per" }, ["/mnd"])]),
    ]))
  }
  return wrap
}

function toMonthly(sub) {
  if (sub.cycle === "yearly") return sub.cost / 12
  if (sub.cycle === "weekly") return (sub.cost * 52) / 12
  return sub.cost
}

function paintMonitor() {
  const body = document.getElementById("monitor-body")
  if (!body) return
  const show = state.settings.hostShow
  const visual = state.settings.monVisual || "spark"
  body.replaceChildren()
  if (state._hostError) body.append(h("p", { class: "err" }, [state._hostError]))
  const sys = state._sys
  if (!sys) {
    body.append(h("p", { class: "empty" }, ["Waiting for the host bridge…"]))
  } else {
    if (sys.browser) {
      body.append(h("p", { class: "muted" }, [sys.note || "Not available in the browser"]))
    }
    if (show.cpu) body.append(metric("CPU", sys.cpuPct, "%", "host:cpu", visual, "var(--green)", sys.cpuPct == null ? (sys.browser ? "Not available in the browser" : "warming up") : sys.cpuCount + " cores"))
    if (show.temp) body.append(metric("CPU temp", sys.cpuTempC, "°C", "host:temp", visual, "var(--amber)"))
    if (show.mem) body.append(metric("Memory", sys.memPct, "%", "host:mem", visual, "var(--cyan)", gib(sys.memUsed) + " / " + gib(sys.memTotal) + " GiB"))
    if (show.load) body.append(metric("Load", sys.load1, "", "host:load", "readout", "var(--violet)", sys.load5 + " · " + sys.load15))
    if (show.gpu) body.append(metric("GPU", sys.gpuPct, "%", "host:gpu", visual, "var(--green)"))
    if (show.gpuTemp) body.append(metric("GPU temp", sys.gpuTempC, "°C", "host:gput", visual, "var(--amber)"))
  }
  for (const mon of state.monitors.filter((m) => m.enabled !== false)) {
    body.append(renderSource(mon))
  }
}

function renderSource(mon) {
  const box = h("div", { class: "source" }, [h("h3", {}, [mon.label + " · " + mon.type])])
  const sample = state._samples?.[mon.id]
  const fields = mon.show?.length ? mon.show : FIELD_OPTIONS[mon.type] || ["value"]
  const visual = mon.visual || (mon.type === "window" || mon.type === "api" ? "readout" : "spark")
  if (!sample) {
    box.append(h("p", { class: "empty" }, ["polling…"]))
    return box
  }
  if (sample.error) box.append(h("p", { class: "err" }, [sample.error]))
  if (mon.type === "app") {
    if (!sample.count) box.append(h("p", { class: "empty" }, ["No process matching " + (mon.target || "—")]))
    if (fields.includes("cpu")) box.append(metric("CPU", sample.cpuPct, "%", mon.id + ":cpu", visual, "var(--green)", sample.count + " procs"))
    if (fields.includes("mem")) box.append(metric("Memory", sample.memPct, "%", mon.id + ":mem", visual, "var(--cyan)"))
    if (fields.includes("rss")) box.append(metric("RSS", sample.rssMb, "MB", mon.id + ":rss", visual, "var(--amber)"))
    if (fields.includes("threads")) box.append(metric("Threads", sample.threads, "", mon.id + ":thr", visual, "var(--violet)"))
    if (fields.includes("pid")) box.append(readout("PID", sample.pid ?? "—"))
    if (fields.includes("state")) box.append(readout("State", sample.state || "—"))
    if (fields.includes("count")) box.append(readout("Count", sample.count))
  }
  if (mon.type === "window") {
    const win = sample.win
    if (!win) box.append(h("p", { class: "empty" }, ["That window is not open."]))
    else {
      if (fields.includes("caption")) box.append(readout("Title", win.caption))
      if (fields.includes("class")) box.append(readout("Class", win.cls || win.name || "—"))
      if (fields.includes("active")) box.append(metric("Focused", win.active ? 100 : 0, "%", mon.id + ":act", visual === "readout" ? "readout" : visual, "var(--green)"))
      if (fields.includes("minimized")) box.append(readout("Minimized", win.minimized ? "yes" : "no"))
      if (fields.includes("size")) box.append(readout("Size", win.w + "×" + win.h))
      if (fields.includes("pid")) box.append(readout("PID", win.pid || "—"))
    }
  }
  if (mon.type === "api") {
    const num = Number(String(sample.value ?? "").replace(/[^0-9.+-]/g, ""))
    if (Number.isFinite(num) && visual !== "readout") {
      box.append(metric(mon.field || "value", num, "", mon.id + ":api", visual, "var(--cyan)", String(sample.value).slice(0, 80)))
    } else {
      box.append(readout(mon.field || "value", sample.value ?? "—"))
    }
  }
  return box
}

function metric(label, value, unit, key, visual, color, sub) {
  const numeric = value == null || value === "" ? null : Number(value)
  if (numeric != null && Number.isFinite(numeric)) pushHist(key, numeric)
  const series = hist[key] || []
  const shown = numeric == null || !Number.isFinite(numeric) ? "—" : String(Math.round(numeric * 10) / 10)
  const row = h("div", { class: "metric" }, [
    h("div", { class: "metric-head" }, [
      h("div", {}, [h("div", { class: "label" }, [label]), sub ? h("div", { class: "muted" }, [sub]) : null]),
      h("div", { class: "value" }, [shown, unit ? h("span", { class: "unit" }, [unit]) : null]),
    ]),
  ])
  if (visual === "spark" && series.length > 1) row.append(spark(series, color))
  if ((visual === "bar" || visual === "gauge") && numeric != null) {
    const pct = Math.max(0, Math.min(100, numeric))
    if (visual === "bar") {
      row.append(h("div", { class: "meter" }, [h("i", { style: `width:${pct}%;background:${color}` })]))
    } else {
      row.append(
        h("div", { class: "gauge-wrap" }, [
          h("div", { class: "gauge", style: `--p:${pct};--c:${color}` }, [h("span", {}, [String(Math.round(pct))])]),
        ]),
      )
    }
  }
  return row
}

function readout(label, value) {
  return h("div", { class: "row metric" }, [
    h("span", { class: "label" }, [label]),
    h("span", { class: "value" }, [String(value)]),
  ])
}

function spark(values, color) {
  const w = 120
  const hgt = 34
  const max = Math.max(1, ...values, 100)
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w
    const y = hgt - (Math.max(0, v) / max) * (hgt - 4) - 2
    return x.toFixed(1) + "," + y.toFixed(1)
  }).join(" ")
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg")
  svg.setAttribute("class", "spark")
  svg.setAttribute("viewBox", `0 0 ${w} ${hgt}`)
  svg.setAttribute("preserveAspectRatio", "none")
  const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline")
  line.setAttribute("points", pts)
  line.setAttribute("fill", "none")
  line.setAttribute("stroke", color)
  line.setAttribute("stroke-width", "1.6")
  svg.append(line)
  return svg
}

function pushHist(key, value) {
  const next = [...(hist[key] || []), value]
  hist[key] = next.length > 36 ? next.slice(-36) : next
}

function gib(bytes) {
  return ((bytes || 0) / 1024 / 1024 / 1024).toFixed(1)
}

function programCommand(command, label) {
  const text = String(command || "").trim()
  const name = String(label || "").trim().toLowerCase()
  if (text && text.toLowerCase() !== name) return text
  if (name === "browser") return "https://www.google.com"
  if (name === "laden") return "https://laden.no"
  return text
}

function installPrograms(platform) {
  if (platform === "iphone" || platform === "android") {
    return [
      ["laden", "Laden", "https://laden.no", "#00ff9d"],
      ["maps", "Maps", "https://maps.google.com", "#00e5ff"],
      ["mail", "Mail", "https://mail.google.com", "#ffd166"],
      ["notes", "Notes", "https://keep.google.com", "#a78bfa"],
    ]
  }
  if (platform === "win32") {
    return [
      ["terminal", "Terminal", "wt", "#00e5ff"],
      ["files", "Files", "explorer", "#ffd166"],
      ["laden", "Laden", "https://laden.no", "#00ff9d"],
      ["browser", "Browser", "https://www.google.com", "#a78bfa"],
    ]
  }
  if (platform === "darwin") {
    return [
      ["terminal", "Terminal", "open -a Terminal", "#00e5ff"],
      ["files", "Files", "open ~", "#ffd166"],
      ["laden", "Laden", "open https://laden.no", "#00ff9d"],
      ["browser", "Browser", "open https://www.google.com", "#a78bfa"],
    ]
  }
  return [
    ["terminal", "Terminal", "wezterm || kitty || konsole", "#00e5ff"],
    ["files", "Files", "xdg-open ~", "#ffd166"],
    ["laden", "Laden", "xdg-open https://laden.no", "#00ff9d"],
    ["dolphin", "Dolphin", "dolphin", "#a78bfa"],
  ]
}

function showDragCatch() {
  const catcher = document.getElementById("drag-catch")
  if (!catcher) return
  const windows = state._platform === "win32"
  catcher.hidden = !windows
  if (catcher._bound || !windows) return
  catcher._bound = true
  catcher.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return
    ladenCall("dragWindow")
  })
}

function renderSetup() {
  const btn = document.getElementById("settings-btn")
  if (btn) btn.hidden = true
  board.classList.remove("journal-board")
  board.style.overflow = "auto"
  board.style.gridTemplateRows = ""
  const screen = h("section", { class: "setup-screen" })
  const title = setupUi.step === "autologin"
    ? "This device"
    : setupUi.step === "unseal"
      ? "Open vault.conf"
      : setupUi.step === "gate" || setupUi.step === "unlock"
        ? "Log in"
        : "Create your account"
  const blurb = setupUi.step === "autologin"
    ? "One choice for later launches."
    : setupUi.step === "unlock"
      ? "The vault already on this device."
      : setupUi.step === "unseal"
        ? "This vault.conf is sealed. The password opens it."
        : "The password is also the vault password. It stays on this device."
  screen.append(
    h("p", { class: "mono muted" }, ["LDASH 1.0"]),
    h("p", { class: "hint" }, ["Published by Laden AS"]),
    h("h2", {}, [title]),
    h("p", { class: "hint" }, [blurb]),
  )
  if (setupUi.error) screen.append(h("p", { class: "err" }, [setupUi.error]))
  if (setupUi.step === "gate") screen.append(setupGateStep())
  if (setupUi.step === "unlock") screen.append(setupUnlockStep())
  if (setupUi.step === "unseal") screen.append(setupUnsealStep())
  if (setupUi.step === "account") screen.append(setupAccountStep())
  if (setupUi.step === "name") screen.append(setupNameStep())
  if (setupUi.step === "programs") screen.append(setupProgramStep())
  if (setupUi.step === "autologin") screen.append(setupAutoLoginStep())
  board.replaceChildren(screen)
}

function setupGateStep() {
  const email = h("input", { type: "email", placeholder: "Email", autocomplete: "username" })
  const password = h("input", { type: "password", placeholder: "Password", autocomplete: "current-password" })
  email.value = setupUi.email || state.settings.accountEmail || ""
  password.value = setupUi.password
  const file = h("input", { type: "file", accept: ".conf,.json,.vault.conf,application/json,text/plain", hidden: "hidden" })
  file.onchange = () => {
    const picked = file.files && file.files[0]
    file.value = ""
    if (picked) void importSetupConf(picked)
  }
  const box = h("div", { class: "setup-step" })
  box.append(email, password, h("p", { class: "hint" }, ["No vault.conf on this device yet. Log in fetches the sealed copy from Laden. This device only never calls out."]))
  const go = async (create) => {
    setupUi.email = email.value.trim()
    setupUi.password = password.value
    setupUi.error = ""
    setupUi.fresh = false
    if (create) setupUi.localOnly = false
    state.settings.accountBase = accountBase()
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(setupUi.email)) setupUi.error = "Enter a real email address."
    else if (setupUi.password.length < 8) setupUi.error = "Use at least 8 characters."
    else if (create) setupUi.step = "account"
    else {
      setupUi.busy = true
      renderSetup()
      const accepted = await loginLocalOrCloud(setupUi.email, setupUi.password)
      setupUi.busy = false
      if (!accepted.ok) {
        setupUi.error = accepted.error || "Could not log in."
        renderSetup()
        return
      }
      if (accepted.fresh) {
        setupUi.fresh = true
        setupUi.step = "name"
        renderSetup()
        return
      }
      afterAuthSuccess()
      return
    }
    renderSetup()
  }
  box.append(h("div", { class: "inline" }, [
    h("button", { class: "btn btn-primary", type: "button", onclick: () => { if (!setupUi.busy) void go(false) } }, [setupUi.busy ? "Opening…" : "Log in"]),
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => { if (!setupUi.busy) void go(true) } }, ["Create account"]),
  ]))
  box.append(h("div", { class: "inline" }, [
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => {
      if (setupUi.busy) return
      setupUi.localOnly = true
      setupUi.fresh = false
      setupUi.email = email.value.trim()
      setupUi.password = password.value
      setupUi.error = ""
      setupUi.step = "account"
      renderSetup()
    } }, ["This device only"]),
    h("button", { class: "btn btn-cyan", type: "button", onclick: () => { if (!setupUi.busy) file.click() } }, ["Import vault.conf"]),
    file,
  ]))
  return box
}

function setupUnlockStep() {
  const email = h("input", { type: "email", placeholder: "Email", autocomplete: "username" })
  const password = h("input", { type: "password", placeholder: "Password", autocomplete: "current-password" })
  email.value = setupUi.email || state.settings.accountEmail || ""
  const box = h("div", { class: "setup-step" })
  box.append(email, password, h("p", { class: "hint" }, ["Opens the vault.conf already on this device."]))
  const go = async () => {
    setupUi.email = email.value.trim()
    setupUi.password = password.value
    setupUi.error = ""
    if (setupUi.password.length < 8) setupUi.error = "Use at least 8 characters."
    if (setupUi.error) {
      renderSetup()
      return
    }
    setupUi.busy = true
    renderSetup()
    const accepted = await loginLocalOrCloud(setupUi.email, setupUi.password)
    setupUi.busy = false
    setupUi.password = ""
    if (!accepted.ok) {
      setupUi.error = accepted.error || "Could not log in."
      renderSetup()
      return
    }
    afterAuthSuccess()
  }
  box.append(h("button", { class: "btn btn-primary", type: "button", onclick: () => { if (!setupUi.busy) void go() } }, [setupUi.busy ? "Opening…" : "Log in"]))
  return box
}

function setupUnsealStep() {
  const pass = h("input", { type: "password", placeholder: "Vault password", autocomplete: "current-password" })
  const box = h("div", { class: "setup-step" })
  box.append(pass, h("p", { class: "hint" }, ["The file becomes the vault on this device."]))
  const go = async () => {
    if (!pass.value || pass.value.length < 8) {
      setupUi.error = "Use at least 8 characters."
      renderSetup()
      return
    }
    setupUi.busy = true
    setupUi.error = ""
    renderSetup()
    await finishConfImport(setupUi.pendingRaw, pass.value, true)
    setupUi.busy = false
    if (confUi.status && confUi.status.kind === "err") {
      setupUi.error = confUi.status.text
      renderSetup()
      return
    }
    setupUi.pendingRaw = ""
    setupUi.password = ""
    afterAuthSuccess()
  }
  box.append(h("div", { class: "inline" }, [
    h("button", { class: "btn btn-primary", type: "button", onclick: () => { if (!setupUi.busy) void go() } }, [setupUi.busy ? "Opening…" : "Open"]),
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => { setupUi.step = "gate"; setupUi.error = ""; setupUi.pendingRaw = ""; renderSetup() } }, ["Back"]),
  ]))
  return box
}

async function importSetupConf(file) {
  setupUi.busy = true
  setupUi.error = ""
  renderSetup()
  try {
    const raw = await file.text()
    const parsed = parseVaultConfText(raw)
    if (parsed.sealed) {
      setupUi.pendingRaw = raw
      setupUi.step = "unseal"
      setupUi.busy = false
      renderSetup()
      return
    }
    confUi.importMode = "replace"
    await finishConfImport(raw, "", true)
    setupUi.busy = false
    if (confUi.status && confUi.status.kind === "err") {
      setupUi.error = confUi.status.text
      renderSetup()
      return
    }
    afterAuthSuccess()
  } catch (err) {
    setupUi.busy = false
    setupUi.error = err instanceof Error ? err.message : "Could not read vault.conf"
    renderSetup()
  }
}

function setupAutoLoginStep() {
  const box = h("div", { class: "setup-step" })
  box.append(h("p", { class: "hint" }, ["Sign in automatically next time on this device?"]))
  const choose = async (yes) => {
    state.settings.autoLogin = !!yes
    save()
    const stored = await persistKit()
    if (!stored.local?.ok) {
      state.settings.autoLogin = null
      setupUi.error = stored.local?.error || "Could not write vault.conf."
      renderSetup()
      return
    }
    enterApp()
  }
  box.append(h("div", { class: "inline" }, [
    h("button", { class: "btn btn-primary", type: "button", onclick: () => choose(true) }, ["Yes"]),
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => choose(false) }, ["No"]),
  ]))
  return box
}

function setupAccountStep() {
  const email = h("input", { type: "email", placeholder: "Email", autocomplete: "off" })
  const password = h("input", { type: "password", placeholder: "Password", autocomplete: "new-password" })
  const confirm = h("input", { type: "password", placeholder: "Repeat password", autocomplete: "new-password" })
  email.value = setupUi.email
  password.value = setupUi.password
  confirm.value = setupUi.confirm
  const box = h("div", { class: "setup-step" })
  box.append(email, password, confirm, h("p", { class: "hint" }, [setupUi.localOnly ? "This device only. Nothing is sent to Laden." : "At least 8 characters. Stored only as the vault hash on this device."]))
  box.append(h("button", { class: "btn btn-primary", type: "button", onclick: () => {
    setupUi.email = email.value.trim()
    setupUi.password = password.value
    setupUi.confirm = confirm.value
    setupUi.error = ""
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(setupUi.email)) setupUi.error = "Enter a real email address."
    else if (setupUi.password.length < 8) setupUi.error = "Use at least 8 characters."
    else if (setupUi.password !== setupUi.confirm) setupUi.error = "The two passwords do not match."
    else setupUi.step = "name"
    renderSetup()
  } }, ["Username"]))
  return box
}

function setupNameStep() {
  const username = h("input", { placeholder: "Username", maxlength: "32", autocomplete: "off" })
  const city = h("input", { placeholder: "City for the weather", maxlength: "80" })
  username.value = setupUi.username
  city.value = setupUi.city
  const box = h("div", { class: "setup-step" })
  box.append(username, city)
  box.append(h("div", { class: "inline" }, [
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => { setupUi.step = setupUi.fresh ? "gate" : "account"; setupUi.error = ""; renderSetup() } }, ["Back"]),
    h("button", { class: "btn btn-primary", type: "button", onclick: () => {
      setupUi.username = username.value.trim()
      setupUi.city = city.value.trim()
      setupUi.error = ""
      if (!/^[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}$/.test(setupUi.username)) setupUi.error = "Username needs a letter or number, up to 32 characters."
      else if (setupUi.city.length < 2) setupUi.error = "Enter a city so the weather can load."
      else setupUi.step = "programs"
      renderSetup()
    } }, ["Programs"]),
  ]))
  return box
}

function setupProgramStep() {
  const choices = installPrograms(state._platform || "")
  if (!setupUi.picks) setupUi.picks = Object.fromEntries(choices.map((row) => [row[0], true]))
  const box = h("div", { class: "setup-step" })
  const picks = h("div", { class: "setup-picks" })
  for (const [id, label, command] of choices) {
    const input = h("input", { type: "checkbox" })
    input.checked = setupUi.picks[id] !== false
    input.onchange = () => { setupUi.picks[id] = input.checked }
    picks.append(h("label", { class: "setup-pick" }, [
      input,
      h("span", {}, [label]),
      h("span", { class: "mono muted" }, [programCommand(command, label)]),
    ]))
  }
  box.append(h("p", { class: "hint" }, ["These land on the Programs panel. You can change them later in Control."]), picks)
  box.append(h("div", { class: "inline" }, [
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => { setupUi.step = "name"; setupUi.error = ""; renderSetup() } }, ["Back"]),
    h("button", { class: "btn btn-primary", type: "button", onclick: () => { if (!setupUi.busy) finishSetup(choices) } }, [setupUi.busy ? "Creating…" : "Open Ldash"]),
  ]))
  return box
}

async function finishSetup(choices) {
  if (setupUi.busy) return
  if (state._localVault) {
    setupUi.error = "This device already has a vault.conf."
    setupUi.step = "unlock"
    renderSetup()
    return
  }
  const picked = choices.filter((row) => setupUi.picks[row[0]] !== false)
  if (!picked.length) {
    setupUi.error = "Pick at least one program."
    renderSetup()
    return
  }
  setupUi.busy = true
  setupUi.error = ""
  renderSetup()
  try {
    const password = setupUi.password
    const hash = await hashSecret(password)
    state.settings.vaultMasterHash = hash
    state.settings.callsign = setupUi.username
    state.settings.accountEmail = setupUi.email.trim().toLowerCase()
    state.settings.weatherCity = setupUi.city
    state.settings.localOnly = !!setupUi.localOnly
    state.settings.setupDone = true
    sessionSecret = password
    state.apps = picked.map((row) => ({ id: uid(), label: row[1], command: programCommand(row[2], row[1]), color: row[3] }))
    const made = await ladenCall("prepareUserFolder", { email: setupUi.email, username: setupUi.username })
    if (made && made.ok === false && !/unknown method/i.test(String(made.error || ""))) {
      state.settings.setupDone = false
      setupUi.error = made.error || "The account folder could not be created."
      setupUi.busy = false
      renderSetup()
      return
    }
    state.settings.accountBase = accountBase()
    save()
    // Local vault.conf only on the critical path. Cloud register/push is background.
    const stored = await persistKit()
    if (!stored.local?.ok) {
      state.settings.setupDone = false
      setupUi.error = stored.local?.error || "Could not write vault.conf."
      setupUi.busy = false
      renderSetup()
      return
    }
    void softCloudRegister(setupUi.email, password)
    afterAuthSuccess()
  } catch (err) {
    state.settings.setupDone = false
    setupUi.error = String(err && err.message || err)
    setupUi.busy = false
    renderSetup()
  }
}

function weatherWords(code) {
  const n = Number(code)
  if (n === 0) return "Clear"
  if (n === 1 || n === 2) return "Fair"
  if (n === 3) return "Cloudy"
  if (n === 45 || n === 48) return "Fog"
  if (n >= 51 && n <= 67) return "Rain"
  if (n >= 71 && n <= 77) return "Snow"
  if (n >= 80 && n <= 82) return "Showers"
  if (n >= 95) return "Storm"
  return "Sky"
}

function renderWeather() {
  const wrap = h("div", { class: "weather" })
  const city = h("input", { placeholder: "City", value: state.settings.weatherCity || "" })
  const refresh = h("button", { class: "btn btn-ghost", type: "button" }, ["Refresh"])
  const out = h("div", { class: "weather-read" })
  const run = async () => {
    const name = city.value.trim()
    state.settings.weatherCity = name
    save()
    if (name.length < 2) {
      out.replaceChildren(h("p", { class: "empty" }, ["Enter a city."]))
      return
    }
    out.replaceChildren(h("p", { class: "empty" }, ["Reading the sky…"]))
    const geo = await ladenCall("fetch", {
      url: "https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&format=json&name=" + encodeURIComponent(name),
    })
    const place = geo?.body?.results?.[0]
    if (!geo?.ok || !place) {
      out.replaceChildren(h("p", { class: "empty" }, [geo?.error || "No match for that city."]))
      return
    }
    const cast = await ladenCall("fetch", {
      url: "https://api.open-meteo.com/v1/forecast?current=temperature_2m,weather_code,wind_speed_10m&timezone=auto&latitude=" + place.latitude + "&longitude=" + place.longitude,
    })
    const current = cast?.body?.current
    if (!cast?.ok || !current) {
      out.replaceChildren(h("p", { class: "empty" }, [cast?.error || "Weather is offline."]))
      return
    }
    const temp = current.temperature_2m
    out.replaceChildren(
      h("strong", {}, [place.name + (place.country ? ", " + place.country : "")]),
      h("p", { class: "weather-temp" }, [temp == null ? "—" : Math.round(temp) + "°"]),
      h("p", { class: "mono muted" }, [weatherWords(current.weather_code) + (current.wind_speed_10m == null ? "" : " · wind " + Math.round(current.wind_speed_10m))]),
    )
  }
  refresh.onclick = () => { run() }
  city.onchange = () => { run() }
  wrap.append(h("div", { class: "inline" }, [city, refresh]), out)
  run()
  return wrap
}

async function hydrateLocalVault() {
  state._localVault = false
  state._sealedPending = false
  state._sealedLocal = ""
  const res = await ladenCall("loadVaultConf")
  if (!res || res.ok === false || !res.exists || !res.text) return
  let parsed
  try {
    parsed = parseVaultConfText(res.text)
  } catch (err) {
    return
  }
  if (parsed.sealed) {
    state._localVault = true
    state._sealedPending = true
    state._sealedLocal = res.text
    return
  }
  try {
    applyVaultConf(parsed, "replace", { quiet: true })
  } catch (err) {
    return
  }
  state._localVault = true
  state._sealedPending = false
}

function routeLaunch() {
  const route = launchRoute(state.settings, { exists: !!state._localVault, sealed: !!state._sealedPending })
  if (route === "board") {
    enterApp()
    return
  }
  setupUi.email = state.settings.accountEmail || ""
  setupUi.step = route === "unlock" ? "unlock" : route === "autologin" ? "autologin" : "gate"
  renderSetup()
}

async function boot() {
  const info = await ladenCall("shortcut")
  if (info?.backend) state._shortcutBackend = info.backend
  if (info?.platform) state._platform = info.platform
  if (info?.shortcut) {
    state.settings.shortcut = info.shortcut
    if (shortcutPill) shortcutPill.textContent = info.shortcut
  }
  await ladenCall("setShortcut", { shortcut: state.settings.shortcut })
  const pause = await ladenCall("pauseMode")
  if (typeof pause?.enabled === "boolean") state.settings.pauseMode = pause.enabled
  await ladenCall("setPauseMode", { enabled: !!state.settings.pauseMode })
  const user = await ladenCall("localUser")
  if (user?.username) vaultUi.localUser = user.username
  showDragCatch()
  await hydrateLocalVault()
  routeLaunch()
}

function startMonitors() {
  if (monitorsOn) return
  monitorsOn = true
  tickMonitors()
  monitorTimer = setInterval(tickMonitors, 2000)
}

async function tickMonitors() {
  const sys = await ladenCall("system")
  if (!sys || sys.ok === false) state._hostError = sys?.error || "Host bridge offline"
  else {
    state._hostError = ""
    state._sys = sys
  }
  const wins = await ladenCall("windows")
  state._wins = wins?.windows || []
  const samples = {}
  for (const mon of state.monitors) {
    if (mon.enabled === false) continue
    if (mon.type === "app") samples[mon.id] = await ladenCall("watchApp", { query: mon.target || "" })
    if (mon.type === "window") samples[mon.id] = { win: matchWindow(state._wins, mon.target || "") }
    if (mon.type === "api" && mon.url) {
      const res = await ladenCall("fetch", { url: mon.url })
      if (!res?.ok) samples[mon.id] = { error: res?.error || "request failed", value: "" }
      else samples[mon.id] = { value: dig(res.body, mon.field) }
    }
  }
  state._samples = samples
  paintMonitor()
}

function matchWindow(windows, target) {
  if (!target) return null
  const [cls, title] = String(target).split("\t")
  return (
    windows.find((w) => {
      const classOk = !cls || w.cls === cls || w.name === cls
      const titleOk = !title || w.caption.toLowerCase().includes(title.toLowerCase())
      return classOk && titleOk
    }) ||
    windows.find((w) => w.caption.toLowerCase().includes(target.toLowerCase()) || w.cls === target) ||
    null
  )
}

function dig(body, path) {
  if (!path) return typeof body === "string" ? body.slice(0, 280) : JSON.stringify(body).slice(0, 280)
  let cur = body
  for (const part of path.split(".")) {
    if (cur && typeof cur === "object" && part in cur) cur = cur[part]
    else return "—"
  }
  return typeof cur === "string" ? cur : JSON.stringify(cur)
}

function accountControl() {
  const s = state.settings
  const box = h("div", {})
  box.append(h("h3", {}, ["Account"]))
  box.append(h("p", { class: "hint" }, [
    s.localOnly
      ? "This device only. The vault is the local vault.conf."
      : s.accountEmail
        ? ("Signed in as " + s.accountEmail + ". A sealed copy stays on Laden when reachable.")
        : "The vault on this device is vault.conf.",
  ]))
  if (s.autoLogin === true) {
    box.append(h("p", { class: "hint" }, ["This device opens straight to the board."]))
  } else if (s.autoLogin === false) {
    box.append(h("p", { class: "hint" }, ["This device asks for the vault password each launch."]))
  }
  const mode = h("button", { class: "btn btn-ghost", type: "button" }, [s.localOnly ? "Use Laden cloud" : "This device only"])
  mode.onclick = async () => {
    s.localOnly = !s.localOnly
    save()
    await persistKit()
    renderSettings()
  }
  box.append(mode)
  if (!s.localOnly && !s.accountToken && s.accountEmail) {
    const password = h("input", { type: "password", placeholder: "Password", autocomplete: "current-password" })
    const note = h("p", { class: "hint" }, ["Sync the sealed cloud copy without leaving Control."])
    const button = h("button", { class: "btn btn-primary", type: "button" }, ["Sync cloud"])
    button.onclick = async () => {
      button.disabled = true
      const accepted = await loginLocalOrCloud(s.accountEmail, password.value)
      password.value = ""
      button.disabled = false
      if (!accepted.ok) {
        note.textContent = accepted.error || "Could not sync."
        return
      }
      note.textContent = "Cloud sync ready."
      renderSettings()
    }
    box.append(password, button, note)
  }
  return box
}

function renderSettings() {
  settingsEl.hidden = !settingsOpen
  syncChrome()
  if (!settingsOpen) {
    settingsEl.replaceChildren()
    return
  }
  const s = state.settings
  const root = h("div", {})
  const saveBtn = h("button", { class: "btn btn-primary", type: "button", id: "save-kit" }, ["Save"])
  const saveNote = h("p", { class: "hint", id: "save-note" }, [
    "Save writes this device's board, vault, journal, and economy into the Laden Ops folder. Only this user can read that file.",
  ])
  saveBtn.onclick = () => saveKitNow(saveBtn, saveNote)
  root.append(
    h("div", { class: "switch-row" }, [
      h("h2", {}, ["Control"]),
      h("div", { class: "cc-actions" }, [
        saveBtn,
        h("button", { class: "btn btn-ghost", type: "button", onclick: () => { settingsOpen = false; renderSettings() } }, ["×"]),
      ]),
    ]),
    saveNote,
    h("p", { class: "hint" }, ["Ldash 1.0 · published by Laden AS · laden.no"]),
  )
  root.append(accountControl())
  if (s.accountEmail) root.append(h("p", { class: "hint" }, ["Account " + s.accountEmail + " · " + (s.callsign || "laden")]))
  root.append(h("h3", {}, ["General"]))
  const accent = dropdown(
    [{ value: "hack", label: "Hack · green" }, { value: "code", label: "Code · cyan" }],
    s.accent,
  )
  accent.onchange = () => {
    s.accent = accent.value
    document.documentElement.dataset.accent = s.accent
    save()
  }
  const shortcut = h("input", { value: s.shortcut })
  const apply = h("button", { class: "btn btn-cyan", type: "button" }, ["Apply shortcut"])
  apply.onclick = async () => {
    s.shortcut = shortcut.value.trim() || "Ctrl+`,Ctrl+|"
    if (shortcutPill) shortcutPill.textContent = s.shortcut
    save()
    const res = await ladenCall("setShortcut", { shortcut: s.shortcut })
    apply.textContent = res?.ok ? "Saved" : "Failed"
    setTimeout(() => { apply.textContent = "Apply shortcut" }, 1200)
  }
  const capacity = h("input", { type: "number", min: "4", max: "40", value: String(s.capacity) })
  capacity.onchange = () => {
    s.capacity = clamp(capacity.value, 4, 40, 14)
    save()
    renderBoard()
    renderSettings()
  }
  root.append(
    field("Accent", accent),
    field("Toggle shortcut", h("div", { class: "inline" }, [shortcut, apply])),
    h("p", { class: "hint" }, [shortcutHint()]),
    toggle("Pause mode (mute game)", !!s.pauseMode, (v) => {
      s.pauseMode = v
      save()
      void ladenCall("setPauseMode", { enabled: v })
    }),
    h("p", { class: "hint" }, ["When on, opening the board mutes system audio and sends media play/pause so games can pause. Closing the board restores mute and resumes."]),
    field("Total size", capacity),
    h("p", { class: "hint" }, ["Every shown panel has to fit inside this total. Size is that panel's share. Fits is the smallest it can shrink and still hold what is inside."]),
    toggle("Edit layout", !!s.editMode, (v) => { s.editMode = v; save(); renderBoard() }),
    toggle("Show calendar", s.showCalendar, (v) => { s.showCalendar = v; save(); renderBoard() }),
    toggle("Show sticky notes", s.showSticky, (v) => { s.showSticky = v; save(); renderBoard() }),
    toggle("Show vault", s.showVault !== false, (v) => { s.showVault = v; save(); renderBoard() }),
    toggle("Flip side", !!s.bSide, (v) => { s.bSide = v; save(); renderBoard(); syncChrome() }),
    h("p", { class: "hint" }, ["Flips the board. On that side, choose the journal or the economy assistant. Caleb reads whichever one you are in."]),
  )
  appendAiControl(root)

  root.append(h("h3", {}, ["Size classes"]))
  root.append(h("p", { class: "hint" }, ["Add or remove classes. Span is that class's share of a row, out of 12. Assign one to each panel below."]))
  for (const klass of s.sizeClasses) {
    const label = h("input", { value: klass.label })
    label.onchange = () => { klass.label = label.value.trim() || klass.id; save(); renderBoard(); renderSettings() }
    const span = h("input", { type: "number", min: "1", max: "12", value: String(klass.span) })
    span.onchange = () => {
      const prev = klass.span
      klass.span = clamp(span.value, 1, 12, klass.span)
      for (const widget of state.grid) {
        if (widget.size !== klass.id || panelSpan(widget) !== prev) continue
        widget.span = klass.span
        if (Number(widget.minSpan) > widget.span) widget.minSpan = widget.span
      }
      save(); renderBoard(); renderSettings()
    }
    const del = h("button", { class: "btn btn-danger", type: "button" }, ["×"])
    del.onclick = () => removeClass(klass.id)
    root.append(h("div", { class: "class-row" }, [label, span, del]))
  }
  root.append(h("button", { class: "btn btn-primary", type: "button", onclick: addClass }, ["Add size class"]))

  root.append(h("h3", {}, ["Panels"]))
  root.append(h("p", { class: "hint" }, [fitSummary()]))
  const fitted = new Map(withEffective(visibleItems()).map((g) => [g.id, g.effectiveSpan]))
  for (const widget of state.grid) {
    const meta = KINDS.find((k) => k[0] === widget.kind)
    const select = dropdown(
      s.sizeClasses.map((klass) => ({ value: klass.id, label: klass.label + " · " + klass.span })),
      s.sizeClasses.some((c) => c.id === widget.size) ? widget.size : s.sizeClasses[0].id,
    )
    select.onchange = () => {
      widget.size = select.value
      widget.span = classById(widget.size).span
      if (Number(widget.minSpan) > widget.span) widget.minSpan = widget.span
      save(); renderBoard(); renderSettings()
    }
    const size = h("input", { type: "number", min: "1", max: "12", value: String(panelSpan(widget)) })
    size.onchange = () => {
      widget.span = clamp(size.value, 1, 12, panelSpan(widget))
      if (Number(widget.minSpan) > widget.span) widget.minSpan = widget.span
      const match = s.sizeClasses.find((klass) => klass.span === widget.span)
      if (match) widget.size = match.id
      save(); renderBoard(); renderSettings()
    }
    const fits = h("input", { type: "number", min: "1", max: "12", value: String(panelMin(widget)) })
    fits.onchange = () => {
      widget.minSpan = Math.min(clamp(fits.value, 1, 12, panelMin(widget)), panelSpan(widget))
      save(); renderBoard(); renderSettings()
    }
    const shown = switchButton(widget.visible)
    shown.title = widget.visible ? "Shown" : "Hidden"
    shown.onclick = () => { widget.visible = !widget.visible; save(); renderBoard(); renderSettings() }
    const hold = fitted.get(widget.id)
    const want = panelSpan(widget)
    const note = !widget.visible
      ? "Hidden, so it takes none of the total."
      : hold != null && hold < want
        ? "Set to " + want + ", holds at " + hold + " inside the total."
        : "Takes " + want + " of " + Number(s.capacity || 36) + "."
    root.append(h("div", { class: "panel-size" }, [
      h("div", { class: "list-row" }, [
        h("span", {}, [meta ? meta[1] : widget.kind]),
        select,
        shown,
      ]),
      h("div", { class: "size-fit" }, [
        h("label", {}, ["Size", size]),
        h("label", {}, ["Fits", fits]),
        h("p", { class: "hint fit-note" }, [note]),
      ]),
    ]))
  }

  root.append(h("h3", {}, ["Programs"]))
  for (const app of state.apps) {
    root.append(h("div", { class: "list-row" }, [
      h("span", {}, [app.label]),
      h("button", { class: "btn btn-danger", type: "button", onclick: () => { state.apps = state.apps.filter((a) => a.id !== app.id); save(); renderBoard(); renderSettings() } }, ["×"]),
    ]))
  }
  const appLabel = h("input", { placeholder: "Label" })
  const appCmd = h("input", { placeholder: "Command or https://…" })
  root.append(h("div", { class: "inline" }, [appLabel, appCmd]))
  root.append(h("button", { class: "btn btn-primary", type: "button", onclick: () => {
    if (!appLabel.value.trim() || !appCmd.value.trim()) return
    state.apps.push({ id: uid(), label: appLabel.value.trim(), command: appCmd.value.trim(), color: "#00ff9d" })
    save(); renderBoard(); renderSettings()
  } }, ["Add program"]))

  root.append(h("h3", {}, ["Host monitor"]))
  const visual = dropdown(["spark", "bar", "gauge", "readout"], s.monVisual)
  visual.onchange = () => { s.monVisual = visual.value; save(); paintMonitor() }
  root.append(field("Drawing", visual))
  for (const key of ["cpu", "mem", "load", "temp", "gpu", "gpuTemp"]) {
    root.append(toggle(key, s.hostShow[key] !== false, (v) => { s.hostShow[key] = v; save(); paintMonitor() }))
  }

  root.append(h("h3", {}, ["Extra monitors"]))
  root.append(h("p", { class: "hint" }, ["Pick an app, a window, or an API, then choose what shows."]))
  for (const mon of state.monitors) {
    root.append(h("div", { class: "list-row" }, [
      h("span", {}, [mon.label + " · " + mon.type]),
      h("button", { class: "btn btn-danger", type: "button", onclick: () => { state.monitors = state.monitors.filter((m) => m.id !== mon.id); save(); paintMonitor(); renderSettings() } }, ["×"]),
    ]))
  }
  root.append(monitorForm())

  root.append(h("h3", {}, ["Monthly subs"]))
  root.append(h("p", { class: "hint" }, ["One line per service. The panel total is the cost per month. Yearly and weekly amounts are converted."]))
  for (const sub of state.subscriptions) {
    const cat = SUB_CATEGORIES.find(([id]) => id === sub.category)
    root.append(h("div", { class: "list-row" }, [
      h("span", {}, [sub.name + " · " + formatMoney(toMonthly(sub), sub.currency) + "/mnd" + (cat ? " · " + cat[1] : "")]),
      h("button", { class: "btn btn-danger", type: "button", onclick: () => { state.subscriptions = state.subscriptions.filter((x) => x.id !== sub.id); save(); renderBoard(); renderSettings() } }, ["×"]),
    ]))
  }
  const subName = h("input", { placeholder: "Spotify, YouTube, web app…" })
  const subCost = h("input", { placeholder: "Cost", type: "number" })
  const subCur = h("input", { placeholder: "NOK", value: "NOK" })
  const subCat = dropdown(SUB_CATEGORIES.map(([id, label]) => ({ value: id, label })), "other")
  const subCycle = dropdown(
    [{ value: "monthly", label: "per month" }, { value: "yearly", label: "per year" }, { value: "weekly", label: "per week" }],
    "monthly",
  )
  const subNext = h("input", { type: "date" })
  const subNotes = h("input", { placeholder: "Note" })
  root.append(
    h("div", { class: "inline" }, [subName, subCost, subCur]),
    h("div", { class: "inline" }, [subCat, subCycle]),
    h("div", { class: "inline" }, [subNext, subNotes]),
    h("button", { class: "btn btn-primary", type: "button", onclick: () => {
      if (!subName.value.trim() || !subCost.value) return
      const category = subCat.value || "other"
      state.subscriptions.push(normalizeSub({
        id: uid(),
        name: subName.value.trim(),
        cost: Number(subCost.value),
        currency: subCur.value.trim() || "NOK",
        cycle: subCycle.value,
        category,
        nextBilling: subNext.value,
        notes: subNotes.value.trim(),
        color: SUB_COLOR[category],
      }))
      save(); renderBoard(); renderSettings()
    } }, ["Add monthly sub"]),
  )

  appendVaultControl(root)
  root.append(h("h3", {}, ["Session"]))
  if (state._shortcutBackend === "iphone" || state._shortcutBackend === "android") {
    const which = state._shortcutBackend === "android" ? "Android" : "iPhone"
    root.append(h("p", { class: "hint" }, [`On ${which}, leave Laden Ops with the Home gesture. Notes stay in the app until you delete it.`]))
  } else {
    root.append(h("button", { class: "btn btn-danger", type: "button", onclick: () => ladenCall("quit") }, ["Quit Laden Ops"]))
    root.append(h("p", { class: "hint" }, ["Hide keeps the shortcut alive. Quit stops it until next login."]))
  }
  settingsEl.replaceChildren(root)
  refreshPickers()
}

function monitorForm() {
  const box = h("div", { class: "form monitor-form" })
  const type = dropdown(
    [{ value: "app", label: "App / process" }, { value: "window", label: "Window" }, { value: "api", label: "API" }],
    "app",
  )
  const label = h("input", { placeholder: "Label" })
  const visual = dropdown(["spark", "bar", "gauge", "readout"], "spark")
  const target = h("input", { placeholder: "process name, or leave blank and pick below" })
  const url = h("input", { placeholder: "https://api…" })
  const field = h("input", { placeholder: "json.field.path" })
  const picker = dropdown([{ value: "", label: "Pick a live target…" }], "")
  const checks = h("div", { class: "checks" })
  const chosen = new Set(["cpu", "mem"])

  function syncType() {
    const kind = type.value
    visual.value = kind === "app" ? "spark" : "readout"
    chosen.clear()
    for (const name of kind === "app" ? ["cpu", "mem"] : kind === "window" ? ["caption", "active", "size"] : ["value"]) chosen.add(name)
    checks.replaceChildren()
    for (const name of FIELD_OPTIONS[kind]) {
      const input = h("input", { type: "checkbox" })
      input.checked = chosen.has(name)
      input.onchange = () => { if (input.checked) chosen.add(name); else chosen.delete(name) }
      checks.append(h("label", {}, [input, name]))
    }
    url.hidden = kind !== "api"
    field.hidden = kind !== "api"
    target.hidden = kind === "api"
    picker.hidden = kind === "api"
    fillPicker()
  }
  function fillPicker() {
    const opts = [{ value: "", label: "Pick a live target…" }]
    if (type.value === "app") {
      for (const group of state._procs || []) {
        opts.push({ value: group.comm, label: group.comm + " · " + group.cpuPct + "%" })
      }
    }
    if (type.value === "window") {
      for (const win of state._wins || []) {
        const value = (win.cls || win.name || "") + "\t" + win.caption.slice(0, 80)
        opts.push({ value, label: win.caption.slice(0, 48) + " · " + (win.cls || win.name) })
      }
    }
    picker.setOptions(opts)
  }
  picker.onchange = () => {
    if (picker.value) target.value = picker.value
  }
  type.onchange = syncType
  const add = h("button", { class: "btn btn-cyan", type: "button" }, ["Add monitor"])
  add.onclick = () => {
    const kind = type.value
    if (!label.value.trim()) return
    if (kind === "api" && !url.value.trim()) return
    if (kind !== "api" && !(target.value.trim() || picker.value)) return
    const picked = kind === "window" ? (picker.value || target.value.trim()) : (target.value.trim() || picker.value)
    state.monitors.push({
      id: uid(),
      label: label.value.trim(),
      type: kind,
      target: kind === "api" ? "" : picked,
      url: url.value.trim(),
      field: field.value.trim(),
      show: [...chosen],
      visual: visual.value,
      enabled: true,
    })
    save()
    paintMonitor()
    renderSettings()
  }
  box.append(type, label, visual, target, picker, url, field, checks, add)
  syncType()
  box._fill = fillPicker
  return box
}

async function refreshPickers() {
  const procs = await ladenCall("processes")
  state._procs = procs?.groups || []
  const wins = await ladenCall("windows")
  state._wins = wins?.windows || state._wins || []
  const form = settingsEl.querySelector(".monitor-form")
  if (form?._fill) form._fill()
}

function addClass() {
  const classes = state.settings.sizeClasses
  if (classes.length >= 8) return
  let n = classes.length + 1
  let id = "C" + n
  while (classes.some((c) => c.id === id)) {
    n += 1
    id = "C" + n
  }
  const span = Math.min(12, Math.max(...classes.map((c) => c.span)) + 1)
  classes.push({ id, label: id, span })
  save()
  renderSettings()
}

function removeClass(id) {
  const classes = state.settings.sizeClasses
  if (classes.length <= 1) return
  const removed = classes.find((c) => c.id === id)
  const next = classes.filter((c) => c.id !== id)
  const nearest = [...next].sort((a, b) => Math.abs(a.span - removed.span) - Math.abs(b.span - removed.span))[0]
  state.settings.sizeClasses = next
  for (const widget of state.grid) if (widget.size === id) widget.size = nearest.id
  save()
  renderBoard()
  renderSettings()
}

function field(label, control) {
  return h("label", { class: "form" }, [h("span", { class: "muted" }, [label]), control])
}

function switchButton(on) {
  return h("button", {
    class: "switch" + (on ? " on" : ""),
    type: "button",
    "aria-pressed": on ? "true" : "false",
  })
}

function toggle(label, on, fn) {
  const btn = switchButton(on)
  btn.title = on ? "On" : "Off"
  btn.onclick = () => {
    fn(!on)
    renderSettings()
  }
  return h("div", { class: "switch-row" }, [h("span", {}, [label]), btn])
}

function readDropOptions(list) {
  return (list || []).map((item) => {
    if (item && typeof item === "object") return { value: String(item.value ?? ""), label: String(item.label ?? item.value ?? "") }
    return { value: String(item), label: String(item) }
  })
}

function dropdown(options, value) {
  let items = readDropOptions(options)
  let current = value == null ? "" : String(value)
  let open = false
  let disabled = false
  if (items.length && !items.some((opt) => opt.value === current)) current = items[0].value
  const root = h("div", { class: "drop" })
  const label = h("span", { class: "drop-label" })
  const caret = h("span", { class: "drop-caret", "aria-hidden": "true" }, ["▾"])
  const btn = h("button", { class: "drop-btn", type: "button" }, [label, caret])
  const menu = h("div", { class: "drop-menu", role: "listbox", hidden: true })
  root.append(btn, menu)

  function paint() {
    const hit = items.find((opt) => opt.value === current)
    label.textContent = hit ? hit.label : (current || "—")
    btn.disabled = disabled
    btn.setAttribute("aria-expanded", open ? "true" : "false")
    menu.hidden = !open
    root.classList.toggle("open", open)
    if (open) place()
  }
  function place() {
    const rect = btn.getBoundingClientRect()
    const roomBelow = window.innerHeight - rect.bottom
    const upward = roomBelow < 160 && rect.top > roomBelow
    const room = Math.max(80, Math.min(240, (upward ? rect.top : roomBelow) - 8))
    menu.style.left = rect.left + "px"
    menu.style.width = rect.width + "px"
    menu.style.maxHeight = room + "px"
    if (upward) {
      menu.style.top = "auto"
      menu.style.bottom = (window.innerHeight - rect.top + 4) + "px"
    } else {
      menu.style.bottom = "auto"
      menu.style.top = (rect.bottom + 4) + "px"
    }
  }
  function rebuild() {
    menu.replaceChildren()
    for (const opt of items) {
      const row = h("button", {
        class: "drop-opt" + (opt.value === current ? " on" : ""),
        type: "button",
        role: "option",
      }, [opt.label])
      row.onclick = () => {
        current = opt.value
        open = false
        paint()
        if (typeof root.onchange === "function") root.onchange()
      }
      menu.append(row)
    }
  }
  function close() {
    open = false
    paint()
  }
  btn.onclick = () => {
    if (disabled) return
    open = !open
    if (open) rebuild()
    paint()
  }
  root._close = close
  root._place = () => { if (open) place() }
  root.onchange = null
  Object.defineProperty(root, "value", {
    get() { return current },
    set(next) {
      current = next == null ? "" : String(next)
      if (items.length && !items.some((opt) => opt.value === current)) current = items[0].value
      if (open) rebuild()
      paint()
    },
  })
  root.setOptions = (list) => {
    const keep = current
    items = readDropOptions(list)
    current = items.some((opt) => opt.value === keep) ? keep : (items[0] ? items[0].value : "")
    if (open) rebuild()
    paint()
  }
  Object.defineProperty(root, "disabled", {
    get() { return disabled },
    set(next) {
      disabled = !!next
      if (disabled) open = false
      paint()
    },
  })
  rebuild()
  paint()
  return root
}

function syncChrome() {
  const editing = !!state.settings.editMode && !state.settings.bSide
  document.body.classList.toggle("layout-edit", editing)
  editBanner.hidden = !editing
  const cc = document.getElementById("settings-btn")
  if (!cc) return
  cc.classList.toggle("on", settingsOpen)
  cc.setAttribute("aria-pressed", settingsOpen ? "true" : "false")
}

function tickClock() {
  if (clockEl) clockEl.textContent = new Date().toLocaleTimeString()
}

function iso(date) {
  const z = (n) => String(n).padStart(2, "0")
  return date.getFullYear() + "-" + z(date.getMonth() + 1) + "-" + z(date.getDate())
}

function monthCells(year, month) {
  const first = new Date(year, month, 1)
  const start = new Date(first)
  start.setDate(1 - ((first.getDay() + 6) % 7))
  const cells = []
  for (let i = 0; i < 42; i++) {
    const day = new Date(start)
    day.setDate(start.getDate() + i)
    cells.push(day)
  }
  return cells
}

function h(tag, props = {}, kids = []) {
  const node = document.createElement(tag)
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue
    if (key === "class") node.className = value
    else if (key === "style") node.setAttribute("style", value)
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value)
    else node.setAttribute(key, String(value))
  }
  for (const kid of kids) {
    if (kid == null || kid === false) continue
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)))
  }
  return node
}

async function ladenCall(method, params) {
  if (!window.laden?.call) return { ok: false, error: "bridge missing" }
  try {
    return await window.laden.call(method, params || {})
  } catch (err) {
    return { ok: false, error: String(err) }
  }
}
