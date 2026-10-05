const VAULT_ROOT = { legal: "vf-legal", credentials: "vf-creds", engagements: "vf-eng" }
const CONF_KIND = "laden.vault.conf"
const CONF_VERSION = 1

const vaultUi = {
  mode: null,
  unlocked: false,
  cwd: null,
  selectedId: null,
  revealed: false,
  toast: "",
  toastTimer: 0,
  busy: false,
  localUser: "",
  master: "",
  unlockError: "",
  name: "",
  username: "",
  link: "",
  password: "",
  comment: "",
  engName: "",
  engTarget: "",
  docName: "",
  journalDraft: "",
  pinLabel: "",
  pinValue: "",
  pinKind: "url",
  bodyDraft: "",
}

const confUi = {
  importMode: "replace",
  pendingRaw: null,
  needsSeal: false,
  status: null,
  busy: false,
}

function defaultVaultFs() {
  const t = new Date().toISOString()
  return [
    { id: VAULT_ROOT.legal, parentId: null, kind: "folder", name: "legal", body: "RoE · authorization letters · scope docs. Keep signed proof here.", createdAt: t, updatedAt: t },
    { id: VAULT_ROOT.credentials, parentId: null, kind: "folder", name: "credentials", body: "Engagement credentials and secrets you are authorized to hold.", createdAt: t, updatedAt: t },
    { id: VAULT_ROOT.engagements, parentId: null, kind: "folder", name: "engagements", body: "In-scope targets, journals, and session pins.", createdAt: t, updatedAt: t },
  ]
}

function ensureVaultFs(fs, legacy) {
  const nodes = Array.isArray(fs) && fs.length ? fs.map((n) => ({ ...n })) : defaultVaultFs()
  const byId = new Set(nodes.map((n) => n.id))
  for (const seed of defaultVaultFs()) {
    if (!byId.has(seed.id)) {
      nodes.push(seed)
      byId.add(seed.id)
    }
  }
  const credKeys = new Set(nodes.filter((n) => n.kind === "cred").map((n) => `${n.name}::${n.username || ""}`.toLowerCase()))
  for (const entry of legacy || []) {
    const key = `${entry.name}::${entry.username || ""}`.toLowerCase()
    if (!entry.name || credKeys.has(key)) continue
    const t = new Date().toISOString()
    nodes.push({
      id: entry.id || uid(),
      parentId: VAULT_ROOT.credentials,
      kind: "cred",
      name: entry.name,
      username: entry.username || "",
      password: entry.password || "",
      link: entry.link || "",
      body: entry.comment || entry.body || "",
      createdAt: t,
      updatedAt: t,
    })
    credKeys.add(key)
  }
  return nodes
}

function vaultEntriesFromFs(fs) {
  return (fs || []).filter((n) => n.kind === "cred").map((n) => ({
    id: n.id,
    name: n.name,
    username: n.username || "",
    link: n.link || "",
    password: n.password || "",
    comment: n.body || "",
  }))
}

function childrenOf(fs, parentId) {
  return fs.filter((n) => n.parentId === parentId).sort((a, b) => {
    if (a.kind === "folder" && b.kind !== "folder") return -1
    if (b.kind === "folder" && a.kind !== "folder") return 1
    return a.name.localeCompare(b.name)
  })
}

function pathOf(fs, id) {
  const chain = []
  let cur = fs.find((n) => n.id === id)
  const guard = new Set()
  while (cur && !guard.has(cur.id)) {
    guard.add(cur.id)
    chain.unshift(cur)
    cur = cur.parentId ? fs.find((n) => n.id === cur.parentId) : null
  }
  return chain
}

function collectDescendants(fs, id) {
  const out = []
  const walk = (pid) => {
    for (const child of fs.filter((n) => n.parentId === pid)) {
      out.push(child.id)
      walk(child.id)
    }
  }
  walk(id)
  return out
}

function kindGlyph(kind) {
  if (kind === "folder") return "▣"
  if (kind === "cred") return "◌"
  if (kind === "doc") return "▤"
  if (kind === "engagement") return "◈"
  return "·"
}

async function hashSecret(value) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value))
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("")
}

async function verifySecret(value, hash) {
  if (!hash) return false
  return (await hashSecret(value)) === hash
}

function touchVault() {
  const next = ensureVaultFs(state.vaultFs, [])
  state.vaultFs = next
}

function commitVault() {
  save()
  renderBoard()
  if (settingsOpen) renderSettings()
}

function flashVault(msg) {
  vaultUi.toast = msg
  const el = document.querySelector(".vfs-toast")
  if (el) el.textContent = msg
  clearTimeout(vaultUi.toastTimer)
  vaultUi.toastTimer = setTimeout(() => {
    vaultUi.toast = ""
    const node = document.querySelector(".vfs-toast")
    if (node) node.textContent = ""
  }, 2200)
}

function lockVault() {
  vaultUi.unlocked = false
  vaultUi.revealed = false
  vaultUi.master = ""
  vaultUi.unlockError = ""
  vaultUi.selectedId = null
  renderBoard()
}

function addVaultNode(node) {
  touchVault()
  state.vaultFs.push(node)
  commitVault()
}

function updateVaultNode(id, patch) {
  state.vaultFs = state.vaultFs.map((n) => (n.id === id ? { ...n, ...patch, updatedAt: new Date().toISOString() } : n))
  commitVault()
}

function removeVaultNode(id) {
  const roots = new Set(Object.values(VAULT_ROOT))
  const doomed = new Set([id, ...collectDescendants(state.vaultFs, id)])
  for (const root of roots) doomed.delete(root)
  state.vaultFs = state.vaultFs.filter((n) => !doomed.has(n.id))
  if (vaultUi.selectedId === id) vaultUi.selectedId = null
  commitVault()
}

function addVaultCred(data) {
  const t = new Date().toISOString()
  addVaultNode({
    id: uid(),
    parentId: VAULT_ROOT.credentials,
    kind: "cred",
    name: data.name.trim(),
    username: data.username || "",
    password: data.password || "",
    link: data.link || "",
    body: data.comment || "",
    createdAt: t,
    updatedAt: t,
  })
  flashVault("Credential locked in")
}

function remember(key, el) {
  el.value = vaultUi[key] || ""
  el.addEventListener("input", () => { vaultUi[key] = el.value })
  return el
}

function renderVault() {
  touchVault()
  const fs = state.vaultFs
  const listing = childrenOf(fs, vaultUi.cwd)
  const selected = vaultUi.selectedId ? fs.find((n) => n.id === vaultUi.selectedId) : null
  const crumbs = vaultUi.cwd ? pathOf(fs, vaultUi.cwd) : []
  const wrap = h("div", { class: "vault" })

  const modes = h("div", { class: "vault-modes" })
  for (const [id, label] of [["fs", "Files"], ["add", "Add"]]) {
    modes.append(h("button", {
      class: "btn " + (vaultUi.mode === id ? "btn-primary" : "btn-ghost"),
      type: "button",
      onclick: () => {
        vaultUi.mode = vaultUi.mode === id ? null : id
        vaultUi.unlockError = ""
        vaultUi.master = ""
        vaultUi.cwd = null
        vaultUi.selectedId = null
        renderBoard()
      },
    }, [label]))
  }
  if (vaultUi.unlocked) {
    modes.append(h("button", { class: "btn btn-ghost", type: "button", title: "Lock vault", onclick: lockVault }, ["Lock"]))
  }
  wrap.append(modes)

  if (!vaultUi.mode) {
    wrap.append(h("p", { class: "empty" }, ["Unlock to browse the vault. Authorized work only."]))
    const roots = h("div", { class: "vfs-roots" })
    for (const card of [
      [VAULT_ROOT.legal, "legal", "RoE · auth docs", "▣"],
      [VAULT_ROOT.credentials, "credentials", "secrets · keys", "◌"],
      [VAULT_ROOT.engagements, "engagements", "journals · pins", "◈"],
    ]) {
      roots.append(h("button", {
        class: "vfs-root",
        type: "button",
        onclick: () => {
          vaultUi.mode = "fs"
          vaultUi.cwd = card[0]
          vaultUi.selectedId = null
          vaultUi.unlockError = ""
          renderBoard()
        },
      }, [
        h("span", { class: "vfs-glyph" }, [card[3]]),
        h("span", { class: "vfs-root-name" }, [card[1]]),
        h("span", { class: "muted" }, [card[2]]),
      ]))
    }
    wrap.append(roots, renderConfStrip(true))
  }

  if (vaultUi.mode && !vaultUi.unlocked) wrap.append(renderVaultGate())

  if (vaultUi.unlocked && vaultUi.mode === "fs") wrap.append(renderVaultBrowser(fs, listing, selected, crumbs))

  if (vaultUi.unlocked && vaultUi.mode === "add") {
    if (state.settings.editMode) {
      wrap.append(h("p", { class: "hint" }, ["Leave edit mode to add vault entries."]))
    } else {
      wrap.append(renderVaultAdd())
    }
  }

  wrap.append(h("p", { class: "vfs-toast mono" }, [vaultUi.toast || ""]))
  return wrap
}

function renderVaultGate() {
  const input = h("input", {
    type: "password",
    placeholder: state.settings.vaultMasterHash
      ? "Custom vault master"
      : deviceLock()
        ? "Device lock unlocks the vault"
        : `Local password for ${vaultUi.localUser || "this user"}`,
  })
  remember("master", input)
  const form = h("form", { class: "form vault-gate" }, [
    h("p", { class: "mono muted" }, ["Master password · " + (vaultUi.mode === "add" ? "ADD" : "FILES")]),
    input,
  ])
  if (vaultUi.unlockError) form.append(h("p", { class: "err" }, [vaultUi.unlockError]))
  const button = h("button", { class: "btn btn-primary", type: "submit" }, [vaultUi.busy ? "Checking…" : "Unlock"])
  button.disabled = vaultUi.busy
  form.append(button)
  form.onsubmit = async (event) => {
    event.preventDefault()
    if (!vaultUi.master || vaultUi.busy) return
    vaultUi.busy = true
    vaultUi.unlockError = ""
    renderBoard()
    try {
      const hash = state.settings.vaultMasterHash || ""
      let ok = false
      let message = "Wrong master password."
      if (hash) {
        ok = await verifySecret(vaultUi.master, hash)
      } else {
        const res = await ladenCall("verifyPassword", { password: vaultUi.master })
        ok = !!res?.ok
        if (!ok && res?.method === "username-fallback") {
          message = `Wrong key. PAM is unavailable, so the fallback is your username “${vaultUi.localUser || "user"}”.`
        } else if (!ok && res?.method === "local-authentication") {
          message = res?.error || "Face ID, a fingerprint, or the device passcode was not accepted."
        } else if (!ok) {
          message = "Wrong local user password."
        }
      }
      if (!ok) {
        vaultUi.unlockError = message
        return
      }
      vaultUi.unlocked = true
      vaultUi.master = ""
      vaultUi.unlockError = ""
    } finally {
      vaultUi.busy = false
      renderBoard()
    }
  }
  return form
}

function renderVaultBrowser(fs, listing, selected, crumbs) {
  const box = h("div", { class: "vfs" })
  const bar = h("div", { class: "vfs-bar" })
  const up = h("button", { class: "btn btn-ghost", type: "button" }, ["Up"])
  up.disabled = !vaultUi.cwd
  up.onclick = () => {
    const node = fs.find((n) => n.id === vaultUi.cwd)
    vaultUi.cwd = node?.parentId || null
    vaultUi.selectedId = null
    renderBoard()
  }
  const crumbsEl = h("div", { class: "vfs-crumbs mono" }, [
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => { vaultUi.cwd = null; vaultUi.selectedId = null; renderBoard() } }, ["/"]),
  ])
  for (const crumb of crumbs) {
    crumbsEl.append(h("button", {
      class: "btn btn-ghost",
      type: "button",
      onclick: () => { vaultUi.cwd = crumb.id; vaultUi.selectedId = null; renderBoard() },
    }, [crumb.name + "/"]))
  }
  const folderName = h("input", { placeholder: "New folder" })
  const addFolder = h("button", { class: "btn btn-ghost", type: "button" }, ["+"])
  addFolder.onclick = () => {
    const name = folderName.value.trim()
    if (!name) return
    const t = new Date().toISOString()
    addVaultNode({ id: uid(), parentId: vaultUi.cwd, kind: "folder", name, createdAt: t, updatedAt: t })
    flashVault("Folder added")
  }
  bar.append(up, crumbsEl, folderName, addFolder)
  const here = fs.find((n) => n.id === vaultUi.cwd)
  if (here?.scopeId) {
    const scope = (state.journal?.scopes || []).find((s) => s.id === here.scopeId)
    if (scope) bar.append(h("button", { class: "btn btn-cyan", type: "button", onclick: () => exportScopePdf(scope) }, ["Export PDF"]))
  }
  box.append(bar)

  if (!selected) {
    const list = h("div", { class: "vfs-list" })
    if (!listing.length) list.append(h("p", { class: "empty" }, ["Empty. Add a legal doc, a credential, or an engagement."]))
    for (const node of listing) {
      const row = h("button", { class: "vfs-row", type: "button" }, [
        h("span", { class: "vfs-glyph" }, [kindGlyph(node.kind)]),
        h("span", { class: "vfs-name" }, [node.name]),
      ])
      if (node.kind === "engagement" && node.status) row.append(h("span", { class: "vfs-status st-" + node.status }, [node.status]))
      if (node.kind === "cred") row.append(h("span", { class: "mono muted" }, [node.username || ""]))
      row.onclick = () => {
        if (node.kind === "folder") {
          vaultUi.cwd = node.id
          vaultUi.selectedId = null
        } else {
          vaultUi.selectedId = node.id
          vaultUi.revealed = false
          vaultUi.bodyDraft = node.body || ""
        }
        renderBoard()
      }
      list.append(row)
    }
    box.append(list)
  } else if (selected.kind === "cred") {
    box.append(renderCred(selected))
  } else if (selected.kind === "doc") {
    box.append(renderDoc(selected))
  } else if (selected.kind === "engagement") {
    box.append(renderEngagement(selected))
  }

  box.append(renderConfStrip(true))
  return box
}

function renderCred(node) {
  const box = h("div", { class: "vfs-detail" })
  const actions = h("div", { class: "vault-actions" })
  if (node.link) {
    actions.append(h("button", { class: "btn btn-ghost", type: "button", title: "Open link", onclick: () => ladenCall("launch", { command: node.link }) }, ["Open"]))
  }
  actions.append(h("button", {
    class: "btn btn-ghost",
    type: "button",
    onclick: () => { vaultUi.revealed = !vaultUi.revealed; renderBoard() },
  }, [vaultUi.revealed ? "Hide" : "Reveal"]))
  if (node.username) {
    actions.append(h("button", { class: "btn btn-ghost", type: "button", onclick: () => copyText(node.username) }, ["User"]))
  }
  actions.append(
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => copyText(node.password || "") }, ["Copy"]),
    h("button", { class: "btn btn-danger", type: "button", onclick: () => removeVaultNode(node.id) }, ["×"]),
  )
  box.append(
    h("div", { class: "vault-top" }, [h("strong", {}, [node.name]), actions]),
  )
  if (node.username) box.append(h("div", { class: "mono" }, [node.username]))
  if (node.link) box.append(h("div", { class: "mono muted" }, [node.link.replace(/^https?:\/\//, "")]))
  box.append(h("div", { class: "vault-pass mono" }, [vaultUi.revealed ? (node.password || "") : "••••••••••••"]))
  if (node.body) box.append(h("p", { class: "hint" }, [node.body]))
  box.append(h("button", { class: "btn btn-ghost", type: "button", onclick: () => { vaultUi.selectedId = null; renderBoard() } }, ["Back"]))
  return box
}

function renderDoc(node) {
  const area = h("textarea", { rows: "8" })
  area.value = vaultUi.bodyDraft
  area.oninput = () => { vaultUi.bodyDraft = area.value }
  const actions = [
    h("button", { class: "btn btn-cyan", type: "button", onclick: () => {
      if (node.role === "summary") {
        const watched = watchedScope(state.journal)
        if (watched && watched.id === node.scopeId) {
          state.journal.summary = { ...state.journal.summary, text: vaultUi.bodyDraft, updatedAt: new Date().toISOString(), status: state.journal.summary.status || "ok" }
        }
      }
      updateVaultNode(node.id, { body: vaultUi.bodyDraft })
      flashVault("Saved")
    } }, ["Save"]),
  ]
  if (node.role === "casefile" || node.role === "case") {
    const scope = (state.journal?.scopes || []).find((s) => s.id === node.scopeId)
    if (scope) actions.unshift(h("button", { class: "btn btn-ghost", type: "button", onclick: () => exportScopePdf(scope) }, ["Export PDF"]))
  }
  actions.push(h("button", { class: "btn btn-danger", type: "button", onclick: () => removeVaultNode(node.id) }, ["×"]))
  const hint = node.role === "summary"
    ? "Caleb's thoughts. This file is summary.md."
    : node.role === "casefile" || node.role === "case"
      ? "Combined extractions Caleb wrote for this case. This file is casefile.md."
      : node.role === "extract-file"
        ? "Facts from this dump, written after the first analysis."
        : ""
  return h("div", { class: "vfs-detail" }, [
    h("div", { class: "vault-top" }, [
      h("strong", {}, [node.name]),
      h("div", { class: "vault-actions" }, actions),
    ]),
    hint ? h("p", { class: "hint" }, [hint]) : null,
    area,
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => { vaultUi.selectedId = null; renderBoard() } }, ["Back"]),
  ])
}

function renderEngagement(node) {
  const status = dropdown(["scoped", "active", "paused", "closed"], node.status || "scoped")
  status.onchange = () => updateVaultNode(node.id, { status: status.value })
  const area = h("textarea", { rows: "5", placeholder: "Scope notes, progress, contacts" })
  area.value = vaultUi.bodyDraft
  area.oninput = () => { vaultUi.bodyDraft = area.value }
  const log = remember("journalDraft", h("input", { placeholder: "Progress line" }))
  const pins = h("div", { class: "eng-pins" })
  for (const pin of node.pins || []) {
    pins.append(h("div", { class: "eng-pin" }, [
      h("button", { class: "eng-pin-run", type: "button", onclick: () => runPin(pin, node) }, [
        h("span", { class: "mono" }, [pin.kind]),
        h("span", {}, [pin.label]),
      ]),
      h("button", { class: "btn btn-danger", type: "button", onclick: () => removePin(node.id, pin.id) }, ["×"]),
    ]))
  }
  const pinKind = dropdown(["url", "term", "path"], vaultUi.pinKind)
  pinKind.onchange = () => { vaultUi.pinKind = pinKind.value }
  const pinLabel = remember("pinLabel", h("input", { placeholder: "Label" }))
  const pinValue = remember("pinValue", h("input", {
    placeholder: vaultUi.pinKind === "url" ? "https://…" : vaultUi.pinKind === "path" ? "~/path" : "command to run in the terminal",
  }))
  const lines = h("div", { class: "eng-journal" })
  const journal = [...(node.journal || [])].reverse()
  if (!journal.length) lines.append(h("p", { class: "empty" }, ["No journal lines yet."]))
  for (const line of journal) {
    lines.append(h("div", { class: "eng-line" }, [
      h("span", { class: "mono muted" }, [new Date(line.at).toLocaleString()]),
      h("span", {}, [line.text]),
    ]))
  }
  const box = h("div", { class: "vfs-detail" })
  box.append(
    h("div", { class: "vault-top" }, [
      h("div", {}, [
        h("strong", {}, [node.name]),
        node.target ? h("div", { class: "mono muted" }, [node.target]) : null,
      ]),
      h("div", { class: "vault-actions" }, [
        status,
        h("button", { class: "btn btn-cyan", type: "button", onclick: () => openEngagementTerm(node) }, ["Term"]),
        /^https?:\/\//i.test(node.target || "")
          ? h("button", { class: "btn btn-ghost", type: "button", onclick: () => ladenCall("launch", { command: node.target }) }, ["Open"])
          : null,
        h("button", { class: "btn btn-danger", type: "button", onclick: () => removeVaultNode(node.id) }, ["×"]),
      ]),
    ]),
    h("p", { class: "hint" }, ["Notes and pins for an authorized engagement. A pin opens your browser, a folder, or a terminal with the command you stored."]),
    area,
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => { updateVaultNode(node.id, { body: vaultUi.bodyDraft }); flashVault("Saved") } }, ["Save notes"]),
    h("p", { class: "mono muted" }, ["Session journal"]),
    lines,
    h("div", { class: "inline" }, [
      log,
      h("button", { class: "btn btn-primary", type: "button", onclick: () => appendEngagementLine(node.id) }, ["Log"]),
    ]),
    h("p", { class: "mono muted" }, ["Pins · browser / terminal / path"]),
    pins,
    h("div", { class: "inline" }, [pinKind, pinLabel]),
    pinValue,
    h("button", { class: "btn btn-cyan", type: "button", onclick: () => addPin(node.id) }, ["Pin"]),
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => { vaultUi.selectedId = null; renderBoard() } }, ["Back"]),
  )
  log.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault()
      appendEngagementLine(node.id)
    }
  })
  return box
}

function appendEngagementLine(id) {
  const text = vaultUi.journalDraft.trim()
  if (!text) return
  const node = state.vaultFs.find((n) => n.id === id)
  if (!node) return
  vaultUi.journalDraft = ""
  vaultUi.selectedId = id
  const entry = { id: uid(), at: new Date().toISOString(), text }
  updateVaultNode(id, { journal: [...(node.journal || []), entry] })
}

function addPin(id) {
  const value = vaultUi.pinValue.trim()
  if (!value) return
  const node = state.vaultFs.find((n) => n.id === id)
  if (!node) return
  const pin = { id: uid(), label: vaultUi.pinLabel.trim() || vaultUi.pinKind, kind: vaultUi.pinKind, value }
  vaultUi.pinLabel = ""
  vaultUi.pinValue = ""
  vaultUi.selectedId = id
  updateVaultNode(id, { pins: [...(node.pins || []), pin] })
}

function removePin(id, pinId) {
  const node = state.vaultFs.find((n) => n.id === id)
  if (!node) return
  vaultUi.selectedId = id
  updateVaultNode(id, { pins: (node.pins || []).filter((p) => p.id !== pinId) })
}

async function workspaceFor(node) {
  const res = await ladenCall("vaultEnsureDir", { rel: `engagements/${node.id}` })
  if (res?.ok && node.body) {
    await ladenCall("vaultWriteNote", { rel: `engagements/${node.id}`, filename: "JOURNAL.md", body: node.body })
  }
  return res?.ok ? res.path : ""
}

async function openEngagementTerm(node, command) {
  const cwd = await workspaceFor(node)
  const res = await ladenCall("openTerminal", { cwd, command: command || "" })
  flashVault(res?.ok ? `Terminal · ${res.cwd || "ok"}` : (res?.error || "Terminal failed"))
}

async function runPin(pin, node) {
  if (pin.kind === "url") {
    await ladenCall("launch", { command: pin.value })
    flashVault(`Browser · ${pin.label}`)
    return
  }
  if (pin.kind === "path") {
    const res = await ladenCall("openPath", { path: pin.value })
    flashVault(res?.ok ? "Opened path" : (res?.error || "Path failed"))
    return
  }
  await openEngagementTerm(node, pin.value)
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    flashVault("Copied")
  } catch {
    flashVault("Copy failed")
  }
}

function renderVaultAdd() {
  const name = remember("name", h("input", { placeholder: "Service name" }))
  const username = remember("username", h("input", { placeholder: "Username" }))
  const link = remember("link", h("input", { placeholder: "https://…" }))
  const password = remember("password", h("input", { type: "password", placeholder: "Password / secret" }))
  const comment = remember("comment", h("textarea", { rows: "2", placeholder: "Comment" }))
  const docName = remember("docName", h("input", { placeholder: "RoE · auth letter name" }))
  const engName = remember("engName", h("input", { placeholder: "Engagement name" }))
  const engTarget = remember("engTarget", h("input", { placeholder: "In-scope target or program URL" }))
  return h("div", { class: "form" }, [
    h("p", { class: "mono muted" }, ["Credential"]),
    name, username, link, password, comment,
    h("button", { class: "btn btn-primary", type: "button", onclick: () => {
      if (!vaultUi.name.trim() || !vaultUi.password.trim()) return
      const data = { name: vaultUi.name, username: vaultUi.username.trim(), password: vaultUi.password, link: vaultUi.link.trim(), comment: vaultUi.comment.trim() }
      vaultUi.name = ""
      vaultUi.username = ""
      vaultUi.link = ""
      vaultUi.password = ""
      vaultUi.comment = ""
      vaultUi.mode = "fs"
      vaultUi.cwd = VAULT_ROOT.credentials
      addVaultCred(data)
    } }, ["Lock into credentials/"]),
    h("p", { class: "mono muted" }, ["Legal doc"]),
    docName,
    h("button", { class: "btn btn-cyan", type: "button", onclick: () => {
      if (!vaultUi.docName.trim()) return
      const t = new Date().toISOString()
      const name = vaultUi.docName.trim()
      vaultUi.docName = ""
      vaultUi.mode = "fs"
      vaultUi.cwd = VAULT_ROOT.legal
      vaultUi.selectedId = null
      addVaultNode({
        id: uid(), parentId: VAULT_ROOT.legal, kind: "doc", name,
        body: "# Legal / RoE\n\nAuthorization · scope · contacts\n", createdAt: t, updatedAt: t,
      })
      flashVault("Legal doc added")
    } }, ["Add to legal/"]),
    h("p", { class: "mono muted" }, ["Engagement"]),
    engName, engTarget,
    h("button", { class: "btn btn-primary", type: "button", onclick: () => {
      if (!vaultUi.engName.trim()) return
      const t = new Date().toISOString()
      const name = vaultUi.engName.trim()
      const target = vaultUi.engTarget.trim()
      const id = uid()
      const body = `# ${name}\n\n## Scope\n- \n\n## Progress\n- \n\n## Notes\n`
      vaultUi.engName = ""
      vaultUi.engTarget = ""
      vaultUi.mode = "fs"
      vaultUi.cwd = VAULT_ROOT.engagements
      vaultUi.selectedId = id
      vaultUi.bodyDraft = body
      addVaultNode({
        id, parentId: VAULT_ROOT.engagements, kind: "engagement", name, target,
        status: "scoped", body, pins: [], journal: [], createdAt: t, updatedAt: t,
      })
      flashVault("Engagement opened")
    } }, ["Open engagement"]),
  ])
}

function confFileName(callsign) {
  const slug = (callsign || "laden").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "laden"
  return slug + ".vault.conf"
}

const CONF_CHUNK = 240000

function kitCopy(value) {
  return JSON.parse(JSON.stringify(value == null ? null : value))
}

function deviceLock() {
  return state._shortcutBackend === "iphone" || state._shortcutBackend === "android"
}

function unknownMethod(res) {
  return !!res && res.ok === false && /unknown method/i.test(String(res.error || ""))
}

function buildVaultConf() {
  const callsign = (state.settings.callsign || "laden").trim() || "laden"
  const label = (state.settings.kitLabel || "personal ops kit").trim() || "personal ops kit"
  const s = state.settings
  const vaultFs = kitCopy(state.vaultFs)
  return {
    kind: CONF_KIND,
    version: CONF_VERSION,
    sealed: false,
    meta: { callsign, label, exportedAt: new Date().toISOString() },
    settings: {
      accent: s.accent,
      showCalendar: s.showCalendar,
      showSticky: s.showSticky,
      showVault: s.showVault,
      vaultMasterHash: s.vaultMasterHash || "",
      capacity: s.capacity,
      shortcut: s.shortcut,
      pauseMode: !!s.pauseMode,
      callsign,
      kitLabel: label,
      bSide: !!s.bSide,
      flipView: s.flipView === "economy" ? "economy" : "journal",
      aiProvider: s.aiProvider || "xai",
      aiApiKey: s.aiApiKey || "",
      monVisual: s.monVisual,
      accountEmail: s.accountEmail || "",
      accountBase: s.accountBase || "",
      confUrl: s.confUrl || "",
      weatherCity: s.weatherCity || "",
      autoLogin: s.autoLogin === true ? true : s.autoLogin === false ? false : null,
      localOnly: !!s.localOnly,
      setupDone: !!s.setupDone,
      sizeClasses: kitCopy(s.sizeClasses),
      hostShow: kitCopy(s.hostShow || {}),
    },
    grid: kitCopy(state.grid),
    apps: kitCopy(state.apps),
    monitors: kitCopy(state.monitors),
    events: kitCopy(state.events),
    sticky: state.sticky,
    subscriptions: kitCopy(state.subscriptions),
    qdues: kitCopy(state.qdues),
    economy: kitCopy(state.economy),
    journal: ensureJournal(kitCopy(state.journal)),
    vault: vaultEntriesFromFs(vaultFs),
    vaultFs,
  }
}

function serializeConf(file) {
  const sealNote = file.sealed ? "sealed · AES-GCM" : "plain · keep offline"
  const header = [
    `// laden.vault.conf — ${file.meta.callsign || "laden"}'s ${file.meta.label || "personal ops kit"}`,
    `// stamped ${file.meta.exportedAt} · ${sealNote}`,
    "// carry your ops · do not commit secrets",
    "",
  ].join("\n")
  return header + JSON.stringify(file, null, 2) + "\n"
}

function bytesToB64(bytes) {
  let s = ""
  for (const b of bytes) s += String.fromCharCode(b)
  return btoa(s)
}

function b64ToBytes(text) {
  const bin = atob(text)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

async function deriveConfKey(passphrase, salt) {
  const material = await crypto.subtle.importKey("raw", new TextEncoder().encode(passphrase), "PBKDF2", false, ["deriveKey"])
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt, iterations: 120000, hash: "SHA-256" },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  )
}

async function sealVaultConf(payload, passphrase) {
  const salt = crypto.getRandomValues(new Uint8Array(16))
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const key = await deriveConfKey(passphrase, salt)
  const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(JSON.stringify(payload)))
  return {
    kind: CONF_KIND,
    version: CONF_VERSION,
    sealed: true,
    meta: { ...payload.meta },
    salt: bytesToB64(salt),
    iv: bytesToB64(iv),
    ciphertext: bytesToB64(new Uint8Array(cipher)),
  }
}

function parseVaultConfText(raw) {
  const trimmed = String(raw || "").replace(/^\uFEFF/, "").trim()
  const start = trimmed.search(/[\[{]/)
  if (start < 0) throw new Error("Empty or unreadable vault.conf")
  const parsed = JSON.parse(trimmed.slice(start))
  if (parsed?.kind !== CONF_KIND) throw new Error("Not a laden.vault.conf file")
  if (parsed.version !== CONF_VERSION) throw new Error("Unsupported vault.conf version")
  return parsed
}

async function resolveVaultConf(file, passphrase) {
  if (!file.sealed) return file
  if (!passphrase) throw new Error("This vault.conf is sealed. Enter the transfer passphrase.")
  try {
    const salt = b64ToBytes(file.salt)
    const iv = b64ToBytes(file.iv)
    const key = await deriveConfKey(passphrase, salt)
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, b64ToBytes(file.ciphertext))
    const parsed = JSON.parse(new TextDecoder().decode(plain))
    if (parsed.kind !== CONF_KIND) throw new Error("Not a laden vault.conf")
    return parsed
  } catch (err) {
    if (err instanceof Error && /vault\.conf|version|passphrase/.test(err.message)) throw err
    throw new Error("Wrong seal passphrase, or the file is damaged.")
  }
}

function detachFiles(nodes) {
  const pending = []
  for (const node of nodes || []) {
    if (!node || typeof node.file !== "string" || !node.file) continue
    pending.push({ id: node.id, name: node.name, parentId: node.parentId, file: node.file })
    delete node.file
  }
  return pending
}

async function readVaultFileB64(rel, filename) {
  const open = await ladenCall("vaultReadOpen", { rel, filename })
  if (!open?.ok) return { ok: false, error: open?.error || "read failed", unknown: unknownMethod(open) }
  let data = ""
  let offset = 0
  for (let guard = 0; guard < 400; guard++) {
    const chunk = await ladenCall("vaultReadChunk", { token: open.token, offset })
    if (!chunk?.ok) return { ok: false, error: chunk?.error || "read failed" }
    data += chunk.data || ""
    if (chunk.done) return { ok: true, data }
    offset = Number(chunk.next) || data.length
  }
  return { ok: false, error: "file read did not finish" }
}

async function attachSourceFiles(vaultFs) {
  const byId = new Map((vaultFs || []).map((node) => [node.id, node]))
  let skipped = false
  for (const node of vaultFs || []) {
    if (!node || node.role !== "source-doc") continue
    const docs = byId.get(node.parentId)
    const folder = docs && byId.get(docs.parentId)
    if (!docs || docs.role !== "documents" || !folder) continue
    const rel = "engagements/" + folder.id + "/documents"
    const read = await readVaultFileB64(rel, node.name || "file")
    if (read.unknown) {
      skipped = true
      continue
    }
    if (read.ok && read.data) node.file = read.data
  }
  return skipped
}

async function writeVaultFileB64(rel, filename, data) {
  const body = String(data || "")
  if (body.length <= CONF_CHUNK) return ladenCall("vaultWriteFile", { rel, filename, data: body })
  const open = await ladenCall("vaultWriteOpen", { rel, filename })
  if (!open?.ok) {
    if (unknownMethod(open)) return ladenCall("vaultWriteFile", { rel, filename, data: body })
    return open
  }
  for (let i = 0; i < body.length; i += CONF_CHUNK) {
    const chunk = await ladenCall("vaultWriteChunk", { token: open.token, data: body.slice(i, i + CONF_CHUNK) })
    if (!chunk?.ok) return chunk
  }
  return ladenCall("vaultWriteFinish", { token: open.token })
}

async function syncImportedVault(pending) {
  const scopes = state.journal?.scopes || []
  for (const scope of scopes) {
    const pack = ensureScopeVault(scope)
    const rel = "engagements/" + pack.folder.id
    await ladenCall("vaultEnsureDir", { rel: rel + "/dump" })
    await ladenCall("vaultEnsureDir", { rel: rel + "/extracted" })
    await ladenCall("vaultEnsureDir", { rel: rel + "/documents" })
    await ladenCall("vaultWriteNote", { rel, filename: "casefile.md", body: pack.caseDoc?.body || "" })
    await ladenCall("vaultWriteNote", { rel, filename: "summary.md", body: pack.summary?.body || "" })
    const profile = (state.vaultFs || []).find((node) => node.parentId === pack.folder.id && node.role === "profile")
    if (profile) await ladenCall("vaultWriteNote", { rel, filename: "PROFILE.md", body: profile.body || "" })
    const notes = (state.vaultFs || []).filter((node) => node.parentId === pack.dump?.id && node.role === "dump-note")
    for (const note of notes) {
      const name = "dump-" + String(note.id).slice(0, 8) + ".md"
      await ladenCall("vaultWriteNote", {
        rel: rel + "/dump",
        filename: name,
        body: "# " + (note.name || "dump") + "\n\n" + (note.body || ""),
      })
    }
    const holders = (state.vaultFs || []).filter((node) => node.parentId === pack.extracted?.id && node.role === "extract")
    for (const holder of holders) {
      const file = (state.vaultFs || []).find((node) => node.parentId === holder.id && node.role === "extract-file")
      if (!file) continue
      const extractRel = rel + "/extracted/" + holder.id
      await ladenCall("vaultEnsureDir", { rel: extractRel })
      await ladenCall("vaultWriteNote", { rel: extractRel, filename: "casefile.md", body: file.body || "" })
    }
  }
  const byId = new Map((state.vaultFs || []).map((node) => [node.id, node]))
  for (const item of pending || []) {
    const node = byId.get(item.id)
    const docs = node && byId.get(node.parentId)
    const folder = docs && byId.get(docs.parentId)
    if (!folder || !item.file) continue
    const written = await writeVaultFileB64("engagements/" + folder.id + "/documents", item.name || "file", item.file)
    if (written && written.ok === false && !unknownMethod(written)) {
      throw new Error(written.error || "A case document could not be stored")
    }
    if (node) delete node.file
  }
  save()
}

async function deliverConf(text, filename) {
  if (!deviceLock()) {
    downloadText(text, filename)
    return { ok: true }
  }
  const open = await ladenCall("saveTextOpen", { filename })
  if (!open?.ok) {
    if (unknownMethod(open)) {
      downloadText(text, filename)
      return { ok: true }
    }
    throw new Error(open?.error || "Could not save the kit file")
  }
  const body = String(text || "")
  for (let i = 0; i < body.length; i += CONF_CHUNK) {
    const chunk = await ladenCall("saveTextChunk", { token: open.token, data: body.slice(i, i + CONF_CHUNK) })
    if (!chunk?.ok) throw new Error(chunk?.error || "Could not save the kit file")
  }
  const done = await ladenCall("saveTextFinish", { token: open.token })
  if (!done?.ok) throw new Error(done?.error || "Could not share the kit file")
  return done
}

function applyVaultConf(payload, mode, opts) {
  const preserveDevice = !!(opts && opts.preserveDevice)
  const prevAuto = state.settings.autoLogin
  const prevLocal = !!state.settings.localOnly
  const incomingFs = Array.isArray(payload.vaultFs) && payload.vaultFs.length ? payload.vaultFs : null
  const pending = detachFiles(incomingFs)
  if (Array.isArray(payload.files)) {
    for (const item of payload.files) {
      if (item && typeof item.file === "string" && item.file) pending.push(item)
    }
  }
  let fs
  if (mode === "merge-vault") {
    const map = new Map((state.vaultFs || []).map((n) => [n.id, n]))
    for (const node of incomingFs || []) map.set(node.id, node)
    fs = ensureVaultFs([...map.values()], payload.vault || [])
  } else {
    fs = ensureVaultFs(incomingFs || defaultVaultFs(), payload.vault || [])
  }
  state.vaultFs = fs
  const settings = payload.settings || {}
  const allow = ["accent", "showCalendar", "showSticky", "showVault", "vaultMasterHash", "capacity", "shortcut", "pauseMode", "callsign", "kitLabel", "bSide", "flipView", "aiProvider", "aiApiKey", "monVisual", "accountEmail", "accountBase", "confUrl", "weatherCity", "autoLogin", "localOnly"]
  for (const key of allow) {
    if ((key === "vaultMasterHash" || key === "aiApiKey") && typeof settings[key] === "string") {
      state.settings[key] = settings[key]
      continue
    }
    if (settings[key] != null && settings[key] !== "") state.settings[key] = settings[key]
  }
  if (Array.isArray(settings.sizeClasses) && settings.sizeClasses.length) {
    state.settings.sizeClasses = normalizeClasses(settings.sizeClasses)
  }
  if (settings.hostShow && typeof settings.hostShow === "object") {
    const show = { ...(state.settings.hostShow || {}) }
    for (const key of ["cpu", "mem", "load", "temp", "gpu", "gpuTemp"]) {
      if (typeof settings.hostShow[key] === "boolean") show[key] = settings.hostShow[key]
    }
    state.settings.hostShow = show
  }
  if (settings.setupDone === true) state.settings.setupDone = true
  if (settings.autoLogin === true || settings.autoLogin === false) state.settings.autoLogin = settings.autoLogin
  if (typeof settings.localOnly === "boolean") state.settings.localOnly = settings.localOnly
  if (typeof settings.pauseMode === "boolean") state.settings.pauseMode = settings.pauseMode
  if (preserveDevice) {
    state.settings.autoLogin = prevAuto
    state.settings.localOnly = prevLocal
  }
  if (mode !== "merge-vault" && payload.journal) state.journal = ensureJournal(payload.journal)
  if (payload.meta?.callsign && !state.settings.callsign) state.settings.callsign = payload.meta.callsign
  if (payload.meta?.label && !state.settings.kitLabel) state.settings.kitLabel = payload.meta.label
  if (mode !== "merge-vault") {
    if (Array.isArray(payload.apps)) state.apps = payload.apps
    if (Array.isArray(payload.events)) state.events = payload.events
    if (Array.isArray(payload.subscriptions)) state.subscriptions = payload.subscriptions.map(normalizeSub)
    if (Array.isArray(payload.qdues)) state.qdues = payload.qdues.map(normalizeQdue).filter((ticket) => ticket.title)
    if (payload.economy) state.economy = ensureEconomy(payload.economy)
    if (typeof payload.sticky === "string") state.sticky = payload.sticky
    else if (payload.sticky && typeof payload.sticky.text === "string") state.sticky = payload.sticky.text
    if (Array.isArray(payload.monitors)) {
      state.monitors = payload.monitors.filter((m) => m && (m.type === "app" || m.type === "window" || m.type === "api"))
    }
    if (Array.isArray(payload.grid)) {
      for (const incoming of payload.grid) {
        const ours = state.grid.find((g) => g.kind === incoming.kind || g.id === incoming.id)
        if (!ours) continue
        if (incoming.size) ours.size = incoming.size
        if (Number.isFinite(Number(incoming.span))) ours.span = clamp(incoming.span, 1, 12, panelSpan(ours))
        if (Number.isFinite(Number(incoming.minSpan))) ours.minSpan = Math.min(clamp(incoming.minSpan, 1, 12, panelMin(ours)), panelSpan(ours))
        if (typeof incoming.visible === "boolean") ours.visible = incoming.visible
        if (Number.isFinite(incoming.order)) ours.order = incoming.order
      }
    }
  }
  if (state.settings.accent) document.documentElement.dataset.accent = state.settings.accent
  if (state.settings.shortcut && shortcutPill) shortcutPill.textContent = state.settings.shortcut
  vaultUi.unlocked = false
  save()
  if (opts && opts.quiet) return pending
  renderBoard()
  syncChrome()
  if (settingsOpen) renderSettings()
  return pending
}

function downloadText(text, filename) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json;charset=utf-8" }))
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

function confStatus(kind, text) {
  confUi.status = { kind, text }
  clearTimeout(confUi.timer)
  confUi.timer = setTimeout(() => { confUi.status = null }, 4200)
}

var sessionSecret = ""

// Fixed Laden cloud for sealed vault.conf copies. Not a user field.
// Primary is Docker api-main. Standby is the systemd copy, read-only for writes.
const CLOUD_ACCOUNT_BASE = "https://laden.no/ldash"
const CLOUD_ACCOUNT_STANDBY = "https://laden.no/ldash-standby"

function accountBase() {
  state.settings.accountBase = CLOUD_ACCOUNT_BASE
  return CLOUD_ACCOUNT_BASE
}

function standbyBase() {
  return CLOUD_ACCOUNT_STANDBY
}

function rewriteAccountUrl(url, fromBase, toBase) {
  const target = String(url || "").trim()
  const src = String(fromBase || "").replace(/\/$/, "")
  const dst = String(toBase || "").replace(/\/$/, "")
  if (!target || !src || !dst) return target
  if (target === src) return dst
  if (target.startsWith(src + "/")) return dst + target.slice(src.length)
  return target
}

function shouldFailoverAccount(res) {
  if (!res || res.ok === false) return true
  const status = Number(res.status || 0)
  if (!status) return true
  if (status === 401 || status === 403 || status === 400 || status === 404 || status === 409) return false
  return status >= 500
}

async function accountFetchOnce(url, method, body, token, timeoutSec) {
  return ladenCall("accountFetch", {
    url,
    method: method || "GET",
    body: body || "",
    token: token || "",
    timeout: timeoutSec || 4,
  })
}

async function accountFetchResilient(url, method, body, token) {
  const primary = String(url || "").trim()
  const first = await accountFetchOnce(primary, method, body, token, 3.5)
  if (!shouldFailoverAccount(first)) return Object.assign({}, first, { accountVia: "primary" })
  const standbyUrl = rewriteAccountUrl(primary, CLOUD_ACCOUNT_BASE, CLOUD_ACCOUNT_STANDBY)
  if (!standbyUrl || standbyUrl === primary) return Object.assign({}, first || {}, { accountVia: "primary" })
  const second = await accountFetchOnce(standbyUrl, method, body, token, 6)
  if (second && second.ok !== false) return Object.assign({}, second, { accountVia: "standby" })
  return Object.assign({}, first || second || {}, { accountVia: "primary" })
}

function parseAccountBody(res) {
  if (!res || res.ok === false) return { ok: false, unreachable: true, error: res?.error || "Could not reach the cloud copy" }
  let body = res.body
  if (typeof body === "string") {
    const start = body.indexOf("{")
    try {
      body = JSON.parse(start >= 0 ? body.slice(start) : body)
    } catch (err) {
      body = null
    }
  }
  if (res.status && Number(res.status) >= 400) {
    return { ok: false, error: (body && body.error) || "The cloud copy refused the request.", status: Number(res.status) }
  }
  return { ok: true, body: body || {}, accountVia: res.accountVia || "primary" }
}

async function composePlainConf() {
  const payload = buildVaultConf()
  const skipped = await attachSourceFiles(payload.vaultFs)
  payload.files = detachFiles(payload.vaultFs)
  return { text: serializeConf(payload), skipped }
}

async function pushAccountConf(plainText) {
  const base = accountBase()
  const token = state.settings.accountToken || ""
  if (!token) return { ok: false, soft: true, error: "Online copy waits until the next cloud sync." }
  if (!sessionSecret) return { ok: false, soft: true, error: "Online copy waits until you unlock again." }
  try {
    const kit = parseVaultConfText(plainText)
    if (kit.settings) {
      kit.settings.autoLogin = null
      kit.settings.localOnly = false
    }
    const sealed = await sealVaultConf(kit, sessionSecret)
    const res = await accountFetchResilient(base + "/v1/vault", "PUT", serializeConf(sealed), token)
    const parsed = parseAccountBody(res)
    if (!parsed.ok) return { ok: false, soft: true, error: parsed.error || "Cloud copy skipped." }
    // Prefer the primary confUrl shape so later pulls hit main first.
    let url = parsed.body.confUrl || state.settings.confUrl || ""
    if (url && parsed.accountVia === "standby") {
      url = rewriteAccountUrl(url, CLOUD_ACCOUNT_STANDBY, CLOUD_ACCOUNT_BASE) || url
    }
    if (url) state.settings.confUrl = url
    save()
    return { ok: true, confUrl: url }
  } catch (err) {
    return { ok: false, soft: true, error: "Cloud copy skipped." }
  }
}

async function persistKit() {
  const made = await composePlainConf()
  const local = await ladenCall("saveVaultConf", { text: made.text })
  // Cloud push never blocks Save / create / login. Soft-fail in the background.
  if (local?.ok && !state.settings.localOnly) void pushAccountConf(made.text)
  return { local, online: { ok: false, soft: true, pending: true }, skipped: made.skipped }
}

async function accountAuth(kind, email, password) {
  const base = accountBase()
  const res = await accountFetchResilient(
    base + (kind === "register" ? "/v1/register" : "/v1/login"),
    "POST",
    JSON.stringify({ email, password }),
    "",
  )
  if (!res || res.ok === false) {
    return { ok: false, unreachable: true, error: res?.error || "Could not reach the cloud copy" }
  }
  const parsed = parseAccountBody(res)
  if (!parsed.ok) return parsed
  const token = parsed.body.token || ""
  let confUrl = parsed.body.confUrl || ""
  if (confUrl && parsed.accountVia === "standby") {
    confUrl = rewriteAccountUrl(confUrl, CLOUD_ACCOUNT_STANDBY, CLOUD_ACCOUNT_BASE) || confUrl
  }
  if (!token || !confUrl) return { ok: false, error: "The cloud copy did not return a vault.conf link." }
  return { ok: true, token, confUrl, standby: !!parsed.body.readOnly || parsed.accountVia === "standby" }
}

async function pullAccountConf(confUrl, password) {
  const res = await accountFetchResilient(confUrl, "GET", "", "")
  if (!res || res.ok === false) return { ok: false, soft: true, error: res?.error || "Could not read the online vault.conf" }
  if (Number(res.status) === 404) return { ok: true, empty: true }
  if (res.status && Number(res.status) >= 400) return { ok: false, soft: true, error: "Could not read the online vault.conf" }
  const raw = typeof res.body === "string" ? res.body : JSON.stringify(res.body || "")
  if (!raw || raw.indexOf("laden.vault.conf") < 0) return { ok: true, empty: true }
  const payload = await resolveVaultConf(parseVaultConfText(raw), password)
  return { ok: true, payload, accountVia: res.accountVia || "primary" }
}

async function exportVaultConf(sealPass) {
  if (!vaultUi.unlocked) {
    confStatus("err", "Unlock the vault first to export secrets.")
    if (settingsOpen) renderSettings()
    else renderBoard()
    return
  }
  confUi.busy = true
  try {
    const payload = buildVaultConf()
    const skippedDocs = await attachSourceFiles(payload.vaultFs)
    payload.files = detachFiles(payload.vaultFs)
    const file = sealPass ? await sealVaultConf(payload, sealPass) : payload
    const name = confFileName(state.settings.callsign)
    await deliverConf(serializeConf(file), name)
    const base = sealPass ? `Sealed ${name}.` : `Exported ${name} in the clear.`
    confStatus("ok", skippedDocs ? `${base} Case documents on disk were left out. Open Laden Ops again, then export once more.` : base)
  } catch (err) {
    confStatus("err", err instanceof Error ? err.message : "Export failed")
  } finally {
    confUi.busy = false
    if (settingsOpen) renderSettings()
    renderBoard()
  }
}

async function finishConfImport(raw, passphrase, quiet) {
  confUi.busy = true
  try {
    const payload = await resolveVaultConf(parseVaultConfText(raw), passphrase)
    const pending = applyVaultConf(payload, confUi.importMode, quiet ? { quiet: true } : null)
    await syncImportedVault(pending)
    if (passphrase) {
      sessionSecret = passphrase
      if (!state.settings.vaultMasterHash) state.settings.vaultMasterHash = await hashSecret(passphrase)
    }
    state.settings.setupDone = true
    state._localVault = true
    state._sealedPending = false
    state._sealedLocal = ""
    save()
    const stored = await persistKit()
    if (!stored.local?.ok) throw new Error(stored.local?.error || "Could not write vault.conf.")
    confUi.pendingRaw = null
    confUi.needsSeal = false
    const who = payload.meta?.callsign || "ops"
    confStatus("ok", confUi.importMode === "merge-vault" ? `Merged ${who}'s vault.` : `Loaded ${who}'s kit.`)
  } catch (err) {
    confStatus("err", err instanceof Error ? err.message : "Import failed")
  } finally {
    confUi.busy = false
    if (!quiet) {
      if (settingsOpen) renderSettings()
      renderBoard()
    }
  }
}

async function onConfFile(file) {
  const raw = await file.text()
  try {
    const parsed = parseVaultConfText(raw)
    if (parsed.sealed) {
      confUi.pendingRaw = raw
      confUi.needsSeal = true
      confStatus("ok", "Sealed kit. Enter the transfer passphrase.")
      if (settingsOpen) renderSettings()
      renderBoard()
      return
    }
    await finishConfImport(raw)
  } catch (err) {
    confStatus("err", err instanceof Error ? err.message : "Could not read vault.conf")
    if (settingsOpen) renderSettings()
    renderBoard()
  }
}

function renderConfStrip(compact) {
  const callsign = state.settings.callsign || "laden"
  const box = h("div", { class: compact ? "vault-conf compact" : "vault-conf" })
  if (compact) {
    box.append(h("div", { class: "mono muted" }, [`${callsign} · ${state.settings.kitLabel || "personal ops kit"}`]))
  }
  const file = h("input", { type: "file", accept: ".conf,.json,.vault.conf,application/json,text/plain", hidden: "hidden" })
  file.onchange = () => {
    const picked = file.files && file.files[0]
    file.value = ""
    if (picked) void onConfFile(picked)
  }
  const seal = h("input", { type: "password", placeholder: compact ? "Seal passphrase" : "Leave empty for a plain file" })
  const actions = h("div", { class: "inline" }, [
    h("button", {
      class: "btn btn-ghost",
      type: "button",
      disabled: confUi.busy || !vaultUi.unlocked ? "disabled" : null,
      onclick: () => exportVaultConf(seal.value.trim()),
    }, ["Export"]),
    h("button", { class: "btn btn-cyan", type: "button", onclick: () => file.click() }, ["Import"]),
  ])
  if (!compact) {
    const call = h("input", { value: callsign, maxlength: "32" })
    call.onchange = () => { state.settings.callsign = call.value.trim() || "laden"; save() }
    const label = h("input", { value: state.settings.kitLabel || "personal ops kit", maxlength: "48" })
    label.onchange = () => { state.settings.kitLabel = label.value.trim() || "personal ops kit"; save() }
    const mode = dropdown([
      { value: "replace", label: "Replace kit" },
      { value: "merge-vault", label: "Merge vault entries" },
    ], confUi.importMode)
    mode.onchange = () => { confUi.importMode = mode.value }
    box.append(
      h("p", { class: "hint" }, ["One file opens on Linux, Windows, Mac, iPhone, and Android. It carries the vault, cases, programs, calendar, sticky note, subscriptions, and the settings that travel with them. Seal it when it leaves the device."]),
      h("div", { class: "inline" }, [call, label]),
      field("On import", mode),
      field("Seal passphrase", seal),
      actions,
      file,
    )
  } else {
    box.append(actions, seal, file)
  }
  if (confUi.needsSeal && confUi.pendingRaw) {
    const pass = h("input", { type: "password", placeholder: "Transfer passphrase" })
    const form = h("form", { class: "inline" }, [
      pass,
      h("button", { class: "btn btn-primary", type: "submit" }, ["Unseal"]),
    ])
    form.onsubmit = (event) => {
      event.preventDefault()
      if (!pass.value) return
      void finishConfImport(confUi.pendingRaw, pass.value)
    }
    box.append(form)
  }
  if (confUi.status) box.append(h("p", { class: confUi.status.kind === "ok" ? "hint" : "err" }, [confUi.status.text]))
  return box
}

function appendAiControl(root) {
  const provider = dropdown([
    { value: "xai", label: "SpaceXAI · grok-4.7" },
    { value: "groq", label: "Groq" },
    { value: "openrouter", label: "OpenRouter" },
  ], state.settings.aiProvider || "xai")
  const key = h("input", { type: "password", value: state.settings.aiApiKey || "", placeholder: "API key, stored on this machine" })
  const saveKey = h("button", { class: "btn btn-primary", type: "button" }, ["Save API key"])
  saveKey.onclick = () => {
    state.settings.aiProvider = provider.value
    state.settings.aiApiKey = key.value.trim()
    save()
    saveKey.textContent = "Saved"
    setTimeout(() => { saveKey.textContent = "Save API key" }, 1200)
    if (state.settings.bSide) renderBoard()
  }
  provider.onchange = () => { state.settings.aiProvider = provider.value; save() }
  root.append(
    field("Caleb AI", provider),
    field("API key", h("div", { class: "inline" }, [key, saveKey])),
    h("p", { class: "hint" }, ["Caleb writes the journal brief only. The key stays here and is sent only to the provider you pick."]),
  )
}

function appendVaultControl(root) {
  root.append(h("h3", {}, ["Vault"]))
  const master = h("input", { type: "password", placeholder: state.settings.vaultMasterHash ? "New master replaces the current one" : deviceLock() ? "Empty uses the device lock" : "Empty uses your login password" })
  const set = h("button", { class: "btn btn-cyan", type: "button" }, ["Set master"])
  set.onclick = async () => {
    if (!master.value.trim()) return
    state.settings.vaultMasterHash = await hashSecret(master.value.trim())
    master.value = ""
    lockVault()
    save()
    renderSettings()
  }
  root.append(field("Custom master password", master), h("div", { class: "inline" }, [
    set,
    state.settings.vaultMasterHash
      ? h("button", { class: "btn btn-ghost", type: "button", onclick: () => { state.settings.vaultMasterHash = ""; lockVault(); save(); renderSettings() } }, [deviceLock() ? "Use device lock" : "Use login password"])
      : null,
  ]))
  root.append(h("p", { class: "hint" }, [deviceLock()
    ? "With no custom master, unlock uses Face ID, a fingerprint, or the device passcode. The text in the unlock box is not checked."
    : "With no custom master, unlock uses this account's login password."]))
  const creds = state.vaultFs.filter((n) => n.kind === "cred")
  for (const cred of creds) {
    root.append(h("div", { class: "list-row" }, [
      h("span", {}, [cred.name + (cred.username ? " · " + cred.username : "")]),
      h("button", { class: "btn btn-danger", type: "button", onclick: () => removeVaultNode(cred.id) }, ["×"]),
    ]))
  }
  const name = h("input", { placeholder: "Service name" })
  const user = h("input", { placeholder: "Username" })
  const link = h("input", { placeholder: "https://…" })
  const pass = h("input", { type: "password", placeholder: "Password" })
  const note = h("input", { placeholder: "Comment" })
  root.append(
    h("p", { class: "mono muted" }, ["Quick-add credential"]),
    h("div", { class: "inline" }, [name, user]),
    h("div", { class: "inline" }, [link, pass]),
    note,
    h("button", { class: "btn btn-primary", type: "button", onclick: () => {
      if (!name.value.trim() || !pass.value.trim()) return
      addVaultCred({ name: name.value, username: user.value.trim(), link: link.value.trim(), password: pass.value, comment: note.value.trim() })
      renderSettings()
    } }, ["Lock into vault"]),
  )
  root.append(h("h3", {}, ["Your kit · vault.conf"]))
  root.append(renderConfStrip(false))
}
