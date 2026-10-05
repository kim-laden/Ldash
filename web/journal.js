const JOURNAL_BUCKETS = [
  ["scope", "Scope", "RoE · in-scope · out-of-scope"],
  ["assets", "Assets", "Hosts · apps · accounts you track"],
  ["timeline", "Timeline", "What happened when"],
  ["findings", "Findings", "Observations · hypotheses"],
  ["evidence", "Evidence", "Snippets · links · hashes"],
  ["openqs", "Open Qs", "Next checks · blockers"],
]

const journalUi = {
  draft: "",
  dragOver: false,
  ctx: null,
  termCmd: "",
  termCwd: "",
  termBusy: false,
  termLog: [],
  scopeKind: "terminal",
  scopeLabel: "",
  scopeValue: "",
  monitorId: "",
  caseSetup: null,
  showMotor: false,
  timer: 0,
}

function defaultJournal() {
  return {
    engagementId: "",
    engagementLabel: "",
    dump: [],
    cards: [],
    summary: { text: "", updatedAt: "", status: "idle" },
    scopes: [],
  }
}

function ensureJournal(raw) {
  const base = defaultJournal()
  if (!raw || typeof raw !== "object") return base
  return {
    engagementId: raw.engagementId || "",
    engagementLabel: raw.engagementLabel || "",
    dump: Array.isArray(raw.dump) ? raw.dump : [],
    cards: Array.isArray(raw.cards) ? raw.cards : [],
    summary: {
      text: raw.summary?.text || "",
      updatedAt: raw.summary?.updatedAt || "",
      status: raw.summary?.status || "idle",
      error: raw.summary?.error || "",
    },
    scopes: Array.isArray(raw.scopes) ? raw.scopes : [],
  }
}

function journalEngagements() {
  return (state.vaultFs || []).filter((n) => n.kind === "engagement")
}

function watchedScope(journal) {
  const scopes = journal?.scopes || []
  return scopes.find((s) => s.id === journalUi.monitorId && s.monitoring)
    || scopes.find((s) => s.monitoring)
    || null
}

function scopePack(scope) {
  if (!scope) return null
  const fs = state.vaultFs || []
  const folder = fs.find((n) => n.id === scope.vaultFolderId)
    || fs.find((n) => n.scopeId === scope.id && n.kind === "folder" && n.parentId === VAULT_ROOT.engagements)
  if (!folder) return null
  return {
    folder,
    dump: fs.find((n) => n.parentId === folder.id && n.role === "dump") || null,
    extracted: fs.find((n) => n.parentId === folder.id && n.role === "extracted") || null,
    caseDoc: fs.find((n) => n.parentId === folder.id && n.role === "casefile")
      || fs.find((n) => n.parentId === folder.id && n.role === "case")
      || null,
    summary: fs.find((n) => n.parentId === folder.id && n.role === "summary") || null,
  }
}

function scopeFolderName(scope) {
  const base = (scope.label || "scope").trim().slice(0, 48) || "scope"
  const taken = (state.vaultFs || []).some((n) => n.parentId === VAULT_ROOT.engagements && n.name === base && n.id !== scope.vaultFolderId)
  if (!taken) return base
  return (base + " " + String(scope.id).slice(0, 4)).slice(0, 56)
}

function ensureScopeVault(scope) {
  touchVault()
  const fs = state.vaultFs
  let folder = fs.find((n) => n.id === scope.vaultFolderId)
    || fs.find((n) => n.scopeId === scope.id && n.kind === "folder" && n.parentId === VAULT_ROOT.engagements)
  const t = new Date().toISOString()
  let changed = false
  if (!folder) {
    folder = {
      id: uid(),
      parentId: VAULT_ROOT.engagements,
      kind: "folder",
      name: scopeFolderName(scope),
      scopeId: scope.id,
      body: scope.kind + " · " + scope.value,
      createdAt: t,
      updatedAt: t,
    }
    fs.push(folder)
    changed = true
  }
  scope.vaultFolderId = folder.id
  let dump = fs.find((n) => n.parentId === folder.id && n.role === "dump")
  if (!dump) {
    dump = { id: uid(), parentId: folder.id, kind: "folder", name: "dump", role: "dump", scopeId: scope.id, createdAt: t, updatedAt: t }
    fs.push(dump)
    changed = true
  }
  let extracted = fs.find((n) => n.parentId === folder.id && n.role === "extracted")
  if (!extracted) {
    extracted = { id: uid(), parentId: folder.id, kind: "folder", name: "extracted", role: "extracted", scopeId: scope.id, createdAt: t, updatedAt: t }
    fs.push(extracted)
    changed = true
  }
  let caseDoc = fs.find((n) => n.parentId === folder.id && n.role === "casefile")
  const legacyCase = fs.find((n) => n.parentId === folder.id && n.role === "case")
  if (!caseDoc && legacyCase) {
    legacyCase.role = "casefile"
    legacyCase.name = "casefile.md"
    legacyCase.updatedAt = t
    caseDoc = legacyCase
    changed = true
  } else if (!caseDoc) {
    caseDoc = { id: uid(), parentId: folder.id, kind: "doc", name: "casefile.md", role: "casefile", scopeId: scope.id, body: "", createdAt: t, updatedAt: t }
    fs.push(caseDoc)
    changed = true
  }
  let summary = fs.find((n) => n.parentId === folder.id && n.role === "summary")
  if (!summary) {
    summary = { id: uid(), parentId: folder.id, kind: "doc", name: "summary.md", role: "summary", scopeId: scope.id, body: "", createdAt: t, updatedAt: t }
    fs.push(summary)
    changed = true
  } else if (summary.name !== "summary.md") {
    summary.name = "summary.md"
    summary.updatedAt = t
    changed = true
  }
  return { folder, dump, extracted, caseDoc, summary, changed }
}

function ensureAllScopeVaults() {
  const scopes = state.journal?.scopes || []
  if (!scopes.length) return false
  let changed = false
  for (const scope of scopes) {
    const pack = ensureScopeVault(scope)
    if (pack.changed) changed = true
  }
  if (changed) {
    save()
    for (const scope of scopes) mirrorScopeFiles(scope)
  }
  return changed
}

function fileDumpIntoScope(scope, item) {
  const pack = ensureScopeVault(scope)
  if ((state.vaultFs || []).some((n) => n.fromDumpId === item.id)) return null
  const title = (String(item.raw || "").split("\n").find((line) => line.trim()) || "dump").slice(0, 64)
  const t = item.createdAt || new Date().toISOString()
  const note = {
    id: uid(),
    parentId: pack.dump.id,
    kind: "doc",
    name: title,
    role: "dump-note",
    scopeId: scope.id,
    fromDumpId: item.id,
    body: item.raw,
    createdAt: t,
    updatedAt: t,
  }
  state.vaultFs.push(note)
  return note
}

function mirrorDumpNote(scope, note) {
  const pack = scopePack(scope)
  if (!pack || !note) return
  const rel = "engagements/" + pack.folder.id + "/dump"
  const name = "dump-" + String(note.id).slice(0, 8) + ".md"
  void ladenCall("vaultEnsureDir", { rel })
  void ladenCall("vaultWriteNote", { rel, filename: name, body: "# " + note.name + "\n\n" + (note.body || "") })
}

function mirrorExtractFile(folderId, holder, file) {
  if (!folderId || !holder || !file) return
  const rel = "engagements/" + folderId + "/extracted/" + holder.id
  void ladenCall("vaultEnsureDir", { rel })
  void ladenCall("vaultWriteNote", { rel, filename: "casefile.md", body: file.body || "" })
}

function mirrorScopeFiles(scope) {
  const pack = scopePack(scope) || ensureScopeVault(scope)
  const rel = "engagements/" + pack.folder.id
  void ladenCall("vaultEnsureDir", { rel: rel + "/dump" })
  void ladenCall("vaultEnsureDir", { rel: rel + "/extracted" })
  void ladenCall("vaultWriteNote", { rel, filename: "casefile.md", body: pack.caseDoc?.body || "" })
  void ladenCall("vaultWriteNote", { rel, filename: "summary.md", body: pack.summary?.body || "" })
  const holders = (state.vaultFs || []).filter((n) => n.parentId === pack.extracted?.id && n.role === "extract")
  for (const holder of holders) {
    const file = (state.vaultFs || []).find((n) => n.parentId === holder.id && n.role === "extract-file")
    if (file) mirrorExtractFile(pack.folder.id, holder, file)
  }
}

function keptText(text) {
  const clean = String(text || "").trim()
  if (!clean || /^\(none\)$/i.test(clean)) return ""
  return clean
}

function splitCaseWork(text) {
  const raw = String(text || "")
  const re = /===(EXTRACT\s+(\S+)|CASEFILE|CASE)===/g
  const marks = []
  let match
  while ((match = re.exec(raw))) {
    marks.push({
      index: match.index,
      end: re.lastIndex,
      kind: match[1].startsWith("EXTRACT") ? "extract" : match[1].toLowerCase(),
      id: String(match[2] || "").replace(/^[`"']+|[`"']+$/g, ""),
    })
  }
  const summary = (marks.length ? raw.slice(0, marks[0].index) : raw).trim()
  const extracts = []
  let casefile = ""
  let facts = ""
  for (let i = 0; i < marks.length; i++) {
    const stop = i + 1 < marks.length ? marks[i + 1].index : raw.length
    const body = raw.slice(marks[i].end, stop).trim()
    if (marks[i].kind === "extract") extracts.push({ id: marks[i].id, body })
    else if (marks[i].kind === "casefile") casefile = body
    else facts = body
  }
  return { summary, extracts, casefile, facts }
}

function mergeCase(prev, facts, scope) {
  const clean = String(facts || "").trim()
  if (!clean || /^\(none\)$/i.test(clean)) return prev || ""
  const block = new Date().toLocaleString() + "\n" + clean.slice(0, 2000)
  const base = String(prev || "").trim()
  if (base.endsWith(clean.slice(0, 2000))) return base
  if (!base) return scope.label + "\n" + scope.kind + " - " + scope.value + "\n\n" + block + "\n"
  return base + "\n\n" + block + "\n"
}

function redactOpsText(text) {
  return String(text || "")
    .replace(/\b(sk-|xai-|gsk_|sk-or-)[A-Za-z0-9_\-]{8,}/g, "[redacted]")
    .replace(/\b(api[_-]?key|password|token|secret)\s*[:=]\s*\S+/gi, "$1: [redacted]")
}

function extractFolderName(parentId, title) {
  const base = String(title || "dump").replace(/[\\/]/g, " ").trim().slice(0, 48) || "dump"
  const taken = (state.vaultFs || []).some((n) => n.parentId === parentId && n.name === base)
  if (!taken) return base
  return (base + " " + String(uid()).slice(0, 4)).slice(0, 56)
}

function applyCaseWork(scope, text) {
  const parts = splitCaseWork(text)
  const now = new Date().toISOString()
  const nextSummary = parts.summary || state.journal.summary?.text || ""
  state.journal.summary = { text: nextSummary, updatedAt: now, status: "ok", error: "" }
  if (!scope) return parts
  const pack = ensureScopeVault(scope)
  const notes = (state.vaultFs || []).filter((n) => n.parentId === pack.dump?.id && n.role === "dump-note")
  for (const block of parts.extracts) {
    const body = keptText(block.body)
    if (!body || !pack.extracted) continue
    const note = notes.find((n) => n.fromDumpId === block.id || n.id === block.id)
    if (!note) continue
    const dumpKey = note.fromDumpId || note.id
    const already = (state.vaultFs || []).some((n) => n.parentId === pack.extracted.id && n.role === "extract" && n.fromDumpId === dumpKey)
    if (already) continue
    const holder = {
      id: uid(),
      parentId: pack.extracted.id,
      kind: "folder",
      name: extractFolderName(pack.extracted.id, note.name),
      role: "extract",
      scopeId: scope.id,
      fromDumpId: dumpKey,
      createdAt: now,
      updatedAt: now,
    }
    state.vaultFs.push(holder, {
      id: uid(),
      parentId: holder.id,
      kind: "doc",
      name: "casefile.md",
      role: "extract-file",
      scopeId: scope.id,
      fromDumpId: dumpKey,
      body,
      createdAt: now,
      updatedAt: now,
    })
  }
  const combined = keptText(parts.casefile)
  state.vaultFs = state.vaultFs.map((node) => {
    if (node.id === pack.summary.id) return { ...node, body: nextSummary, updatedAt: now }
    if (node.id === pack.caseDoc.id) {
      const next = combined || (keptText(parts.facts) ? mergeCase(node.body, parts.facts, scope) : node.body)
      return { ...node, body: next, updatedAt: now }
    }
    return node
  })
  mirrorScopeFiles(scope)
  return parts
}

const CASE_FACTS = [
  ["web", "Web address"],
  ["ip", "IP"],
  ["email", "Email"],
  ["phone", "Phone"],
  ["other", "Other"],
]

function startCaseSetup() {
  journalUi.caseSetup = {
    step: "name",
    name: "",
    about: "",
    facts: ["web", "ip", "email", "phone"].map((kind) => ({ id: uid(), kind, value: "" })),
    files: [],
    busy: false,
    error: "",
  }
  renderBoard()
}

function caseProfileText(scope, files) {
  const facts = scope.facts || []
  const lines = [
    "# " + scope.label,
    "",
    "Target in scope: " + scope.label,
    "",
    "## What you know",
    scope.about || "(nothing noted yet)",
    "",
    "## Profile",
  ]
  if (!facts.length) lines.push("(no profile lines yet)")
  for (const fact of facts) lines.push("- " + fact.kind + ": " + fact.value)
  lines.push("", "## Documents")
  if (!files?.length) lines.push("(none uploaded)")
  else for (const file of files) lines.push("- " + file.name)
  return lines.join("\n")
}

function readCaseFile(file) {
  return new Promise((resolve, reject) => {
    if (file.size > 8_000_000) {
      reject(new Error(file.name + " is over 8 MB"))
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result)
      let binary = ""
      for (let i = 0; i < bytes.length; i += 0x1000) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x1000))
      }
      const textish = /\.(md|txt|log|csv|json)$/i.test(file.name) || String(file.type || "").startsWith("text/")
      resolve({
        name: String(file.name || "document").slice(0, 80),
        size: file.size,
        b64: btoa(binary),
        text: textish && bytes.length < 200000 ? new TextDecoder().decode(bytes).slice(0, 20000) : "",
      })
    }
    reader.onerror = () => reject(reader.error || new Error("Could not read " + file.name))
    reader.readAsArrayBuffer(file)
  })
}

async function takeCaseFiles(fileList) {
  const setup = journalUi.caseSetup
  if (!setup) return
  const room = 8 - setup.files.length
  const incoming = [...fileList].slice(0, Math.max(0, room))
  if (!incoming.length) {
    setup.error = "Eight documents is the limit for one case."
    renderBoard()
    return
  }
  try {
    for (const file of incoming) {
      const read = await readCaseFile(file)
      if (setup.files.some((item) => item.name === read.name)) continue
      setup.files.push(read)
    }
    setup.error = ""
  } catch (err) {
    setup.error = err?.message || "Could not read that document."
  }
  renderBoard()
}

async function openCase(setup) {
  const name = String(setup?.name || "").trim().slice(0, 64)
  if (!name) return null
  const facts = (setup.facts || [])
    .map((fact) => ({
      kind: CASE_FACTS.some(([id]) => id === fact.kind) ? fact.kind : "other",
      value: String(fact.value || "").trim().slice(0, 240),
    }))
    .filter((fact) => fact.value)
  const files = (setup.files || []).slice(0, 8)
  const scope = {
    id: uid(),
    kind: "case",
    label: name,
    value: name,
    monitoring: true,
    about: String(setup.about || "").trim().slice(0, 4000),
    facts,
    createdAt: new Date().toISOString(),
  }
  state.journal.scopes.push(scope)
  journalUi.monitorId = scope.id
  if (!state.journal.engagementLabel) state.journal.engagementLabel = name
  const pack = ensureScopeVault(scope)
  const body = caseProfileText(scope, files)
  const t = new Date().toISOString()
  pack.caseDoc.body = body
  pack.caseDoc.updatedAt = t
  pack.folder.name = scopeFolderName(scope)
  pack.folder.body = "Case · " + name
  pack.folder.updatedAt = t
  state.vaultFs.push({
    id: uid(),
    parentId: pack.folder.id,
    kind: "doc",
    name: "profile",
    role: "profile",
    scopeId: scope.id,
    body,
    createdAt: t,
    updatedAt: t,
  })
  const docs = {
    id: uid(),
    parentId: pack.folder.id,
    kind: "folder",
    name: "documents",
    role: "documents",
    scopeId: scope.id,
    createdAt: t,
    updatedAt: t,
  }
  state.vaultFs.push(docs)
  for (const file of files) {
    state.vaultFs.push({
      id: uid(),
      parentId: docs.id,
      kind: "doc",
      name: file.name,
      role: "source-doc",
      scopeId: scope.id,
      body: file.text || ("Stored with the case.\n" + file.name),
      createdAt: t,
      updatedAt: t,
    })
  }
  const now = t
  state.journal.cards.unshift({
    id: uid(),
    bucket: "scope",
    title: name,
    body: body.slice(0, 2000),
    createdAt: now,
    updatedAt: now,
  })
  for (const fact of facts) {
    state.journal.cards.unshift({
      id: uid(),
      bucket: "assets",
      title: fact.kind + " · " + fact.value.slice(0, 60),
      body: fact.value,
      createdAt: now,
      updatedAt: now,
    })
  }
  for (const file of files) {
    state.journal.cards.unshift({
      id: uid(),
      bucket: "evidence",
      title: file.name,
      body: (file.text || file.name).slice(0, 2000),
      createdAt: now,
      updatedAt: now,
    })
  }
  const filed = new Set((state.vaultFs || []).filter((n) => n.fromDumpId).map((n) => n.fromDumpId))
  for (const item of state.journal.dump) {
    if (!filed.has(item.id)) {
      const note = fileDumpIntoScope(scope, item)
      if (note) mirrorDumpNote(scope, note)
    }
  }
  save()
  const rel = "engagements/" + pack.folder.id
  await ladenCall("vaultEnsureDir", { rel: rel + "/dump" })
  await ladenCall("vaultEnsureDir", { rel: rel + "/extracted" })
  await ladenCall("vaultEnsureDir", { rel: rel + "/documents" })
  await ladenCall("vaultWriteNote", { rel, filename: "casefile.md", body })
  await ladenCall("vaultWriteNote", { rel, filename: "PROFILE.md", body })
  await ladenCall("vaultWriteNote", { rel, filename: "summary.md", body: pack.summary?.body || "" })
  for (const file of files) {
    if (!file.b64) continue
    await ladenCall("vaultWriteFile", { rel: rel + "/documents", filename: file.name, data: file.b64 })
  }
  scheduleBrief()
  flashVault("Case opened in engagements")
  return scope
}

function renderCaseSetup() {
  const setup = journalUi.caseSetup
  const steps = [["name", "Name"], ["profile", "Profile"], ["documents", "Documents"]]
  const box = h("div", { class: "case-setup" })
  box.append(h("div", { class: "case-steps" }, steps.map(([id, label]) => h("span", {
    class: "case-step" + (setup.step === id ? " on" : ""),
  }, [label]))))
  if (setup.step === "name") {
    const name = h("input", { placeholder: "Case name · the target in scope" })
    name.value = setup.name
    name.oninput = () => { setup.name = name.value }
    const about = h("textarea", { rows: "3", placeholder: "What you already know about the subject" })
    about.value = setup.about
    about.oninput = () => { setup.about = about.value }
    box.append(
      h("p", { class: "hint" }, ["Name the case. This is the target the scope is about."]),
      name,
      about,
      h("div", { class: "inline" }, [
        h("button", { class: "btn btn-ghost", type: "button", onclick: () => { journalUi.caseSetup = null; renderBoard() } }, ["Cancel"]),
        h("button", { class: "btn btn-primary", type: "button", onclick: () => {
          if (!setup.name.trim()) { setup.error = "The case needs a name."; renderBoard(); return }
          setup.error = ""
          setup.step = "profile"
          renderBoard()
        } }, ["Profile"]),
      ]),
    )
  } else if (setup.step === "profile") {
    box.append(h("p", { class: "hint" }, ["Add the addresses, numbers, and names you already have. Blank lines are skipped."]))
    for (const fact of setup.facts) {
      const kind = dropdown(CASE_FACTS.map(([id, label]) => ({ value: id, label })), fact.kind)
      kind.onchange = () => { fact.kind = kind.value }
      const value = h("input", { placeholder: "Value" })
      value.value = fact.value
      value.oninput = () => { fact.value = value.value }
      box.append(h("div", { class: "inline" }, [
        kind,
        value,
        h("button", { class: "btn btn-danger", type: "button", onclick: () => {
          setup.facts = setup.facts.filter((row) => row.id !== fact.id)
          renderBoard()
        } }, ["×"]),
      ]))
    }
    box.append(h("div", { class: "inline" }, [
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => { setup.step = "name"; renderBoard() } }, ["Back"]),
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => {
        setup.facts.push({ id: uid(), kind: "other", value: "" })
        renderBoard()
      } }, ["Add line"]),
      h("button", { class: "btn btn-primary", type: "button", onclick: () => { setup.step = "documents"; renderBoard() } }, ["Documents"]),
    ]))
  } else {
    const picker = h("input", { type: "file", multiple: "true" })
    picker.onchange = () => { if (picker.files?.length) void takeCaseFiles(picker.files) }
    const drop = h("div", { class: "case-drop" }, [
      h("p", { class: "hint" }, ["Drop documents you already have, or choose files. They are stored in the case and not opened."]),
      picker,
    ])
    drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("over") })
    drop.addEventListener("dragleave", () => drop.classList.remove("over"))
    drop.addEventListener("drop", (event) => {
      event.preventDefault()
      drop.classList.remove("over")
      if (event.dataTransfer?.files?.length) void takeCaseFiles(event.dataTransfer.files)
    })
    box.append(drop)
    if (!setup.files.length) box.append(h("p", { class: "empty" }, ["No documents yet. You can still open the case."]))
    for (const file of setup.files) {
      box.append(h("div", { class: "case-file" }, [
        h("span", {}, [file.name]),
        h("span", { class: "mono muted" }, [Math.max(1, Math.round(file.size / 1024)) + " KB"]),
        h("button", { class: "btn btn-danger", type: "button", onclick: () => {
          setup.files = setup.files.filter((item) => item !== file)
          renderBoard()
        } }, ["×"]),
      ]))
    }
    box.append(h("div", { class: "inline" }, [
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => { setup.step = "profile"; renderBoard() } }, ["Back"]),
      h("button", {
        class: "btn btn-primary",
        type: "button",
        disabled: setup.busy ? "true" : null,
        onclick: async () => {
          if (setup.busy) return
          setup.busy = true
          setup.error = ""
          renderBoard()
          try {
            const opened = await openCase(setup)
            if (!opened) {
              setup.busy = false
              setup.error = "The case needs a name."
              renderBoard()
              return
            }
            journalUi.caseSetup = null
            renderBoard()
          } catch (err) {
            setup.busy = false
            setup.error = err?.message || "Could not open the case."
            renderBoard()
          }
        },
      }, [setup.busy ? "Opening…" : "Open case"]),
    ]))
  }
  if (setup.error) box.append(h("p", { class: "err" }, [setup.error]))
  return box
}

function addScope(kind, label, value) {
  const trimmed = String(value || "").trim()
  if (!trimmed) return null
  const scope = {
    id: uid(),
    kind,
    label: String(label || "").trim() || kind,
    value: trimmed,
    monitoring: true,
    createdAt: new Date().toISOString(),
  }
  state.journal.scopes.push(scope)
  journalUi.monitorId = scope.id
  ensureScopeVault(scope)
  const filed = new Set((state.vaultFs || []).filter((n) => n.fromDumpId).map((n) => n.fromDumpId))
  for (const item of state.journal.dump) {
    if (!filed.has(item.id)) {
      const note = fileDumpIntoScope(scope, item)
      if (note) mirrorDumpNote(scope, note)
    }
  }
  save()
  mirrorScopeFiles(scope)
  scheduleBrief()
  renderBoard()
  return scope
}

let casePdfBusy = false

async function exportScopePdf(scope) {
  if (!scope || casePdfBusy) return
  casePdfBusy = true
  try {
    const pack = ensureScopeVault(scope)
    save()
    const dumps = (state.vaultFs || [])
      .filter((n) => n.parentId === pack.dump.id && n.role === "dump-note")
      .map((n) => ({ name: n.name, body: redactOpsText(n.body || "") }))
    const res = await ladenCall("exportCasePdf", {
      rel: "engagements/" + pack.folder.id,
      title: pack.folder.name,
      scope: scope.kind + " · " + scope.value,
      summary: redactOpsText(pack.summary?.body || state.journal.summary?.text || ""),
      caseBody: redactOpsText(pack.caseDoc?.body || ""),
      dumps,
    })
    if (res?.ok) {
      pushTermLog("case pdf · " + (res.path || "opened"))
      flashVault("Case PDF opened")
    } else {
      pushTermLog("case pdf failed: " + (res?.error || "unknown"))
      flashVault(res?.error || "PDF failed")
    }
  } finally {
    casePdfBusy = false
  }
}

function pushTermLog(text) {
  journalUi.termLog = [{ id: uid(), at: new Date().toISOString(), text }, ...journalUi.termLog].slice(0, 40)
}

function scheduleBrief() {
  const journal = state.journal
  if (!state.settings.aiApiKey?.trim()) return
  if (!journal.dump.length && !journal.cards.length) return
  clearTimeout(journalUi.timer)
  journalUi.timer = setTimeout(() => { void refreshBrief() }, 2800)
}

function addDump(raw, source) {
  const text = String(raw || "").trim()
  if (!text) return
  const item = { id: uid(), raw: text, source: source || "manual", createdAt: new Date().toISOString() }
  state.journal.dump.unshift(item)
  const scope = watchedScope(state.journal)
  if (scope) {
    const note = fileDumpIntoScope(scope, item)
    if (note) mirrorDumpNote(scope, note)
  }
  save()
  scheduleBrief()
  renderBoard()
}

function fileDump(dumpId, bucket) {
  const item = state.journal.dump.find((d) => d.id === dumpId)
  if (!item) return
  const first = item.raw.split("\n").find((line) => line.trim()) || "Untitled"
  const t = new Date().toISOString()
  state.journal.cards.unshift({
    id: uid(),
    bucket,
    title: first.slice(0, 80),
    body: item.raw,
    fromDumpId: item.id,
    createdAt: t,
    updatedAt: t,
  })
  state.journal.dump = state.journal.dump.filter((d) => d.id !== dumpId)
  journalUi.ctx = null
  save()
  scheduleBrief()
  renderBoard()
}

function buildSummaryPrompt(journal) {
  const cardsBlock = JOURNAL_BUCKETS.map(([id, label]) => {
    const cards = journal.cards.filter((c) => c.bucket === id)
    if (!cards.length) return `## ${label}\n(empty)`
    return `## ${label}\n${cards.map((c) => `- ${c.title}: ${redactOpsText(c.body).slice(0, 500)}`).join("\n")}`
  }).join("\n\n")
  const scopes = journal.scopes || []
  const mon = scopes.filter((s) => s.monitoring)
  const scopeBlock = scopes.length
    ? scopes.map((s) => `- [${s.kind}] ${s.label} → ${s.value}${s.monitoring ? " (MONITORING)" : ""}${s.note ? ` · ${s.note}` : ""}`).join("\n")
    : "(none)"
  const monBlock = mon.length ? mon.map((s) => `- ${s.kind}: ${s.label} @ ${s.value}`).join("\n") : "(none actively monitored)"
  const scope = watchedScope(journal)
  const pack = scope ? scopePack(scope) : null
  const notes = pack?.dump
    ? (state.vaultFs || []).filter((n) => n.parentId === pack.dump.id && n.role === "dump-note")
    : []
  const holders = pack?.extracted
    ? (state.vaultFs || []).filter((n) => n.parentId === pack.extracted.id && n.role === "extract")
    : []
  const filed = new Set(notes.map((n) => n.fromDumpId).filter(Boolean))
  const pending = []
  const done = []
  for (const note of notes.slice(0, 40)) {
    const key = note.fromDumpId || note.id
    const clip = redactOpsText(note.body || "").slice(0, 800)
    const holder = holders.find((n) => n.fromDumpId === key)
    if (!holder) pending.push("id " + key + "\nname " + note.name + "\n" + clip)
    else {
      const file = (state.vaultFs || []).find((n) => n.parentId === holder.id && n.role === "extract-file")
      done.push("id " + key + "\nname " + note.name + "\n" + redactOpsText(file?.body || "").slice(0, 800))
    }
  }
  const loose = journal.dump.filter((d) => !filed.has(d.id)).slice(0, 20)
    .map((d, i) => "[inbox " + (i + 1) + "] " + redactOpsText(d.raw).slice(0, 800))
  const profile = pack
    ? redactOpsText((state.vaultFs || []).find((n) => n.parentId === pack.folder.id && n.role === "profile")?.body || "").slice(0, 2000)
    : ""
  const caseNow = pack ? redactOpsText(pack.caseDoc?.body || "").slice(0, 3000) : ""
  return `You are Caleb — the Laden Ops vault / journal helper. You write living briefs for AUTHORIZED security engagements (bug bounty / contracted test / lab). Never invent exploits or attack steps. Summarize progress clearly. Sign the vibe as Caleb without being cheesy.

Engagement: ${journal.engagementLabel || "(untitled)"}

=== LIVE SCOPES (terminal + browser only) ===
${scopeBlock}

=== CURRENTLY MONITORING ===
${monBlock}

=== ORGANIZED ===
${cardsBlock}

=== WATCHED SCOPE ===
${scope ? scope.kind + " · " + scope.label : "(none)"}

=== PROFILE ===
${profile || "(none)"}

=== CURRENT CASEFILE ===
${caseNow || "(empty)"}

=== DUMPS WAITING FOR A FIRST EXTRACT ===
${pending.join("\n\n") || "(none)"}

=== EXTRACTS ALREADY FILED ===
${done.join("\n\n") || "(none)"}

=== INBOX NOT YET IN THE WATCHED DUMP ===
${loose.join("\n") || "(empty)"}

Write a cool, tight ops brief titled in spirit "Caleb's thoughts" with these sections:
1) STATUS — one line vibe + % sense of triage done
2) SCOPE SNAPSHOT — what is in play (include monitored term/browser scopes if useful)
3) MOVING PIECES — assets / threads in motion
4) SIGNAL — strongest findings so far (facts only)
5) NEXT — 3 concrete open questions / next notes to capture
6) DUMP PRESSURE — how messy the inbox still is

If a monitored terminal cwd or browser URL is relevant to the notes, mention it briefly; do not invent what happened inside them.
Use short lines, mono-friendly punctuation, no markdown tables. Max ~220 words.

After the brief, and only for dumps listed under DUMPS WAITING FOR A FIRST EXTRACT, add one block per dump:
===EXTRACT <id>===
then only facts that dump actually contains. Use the id shown on that dump. If that dump has no useful fact, write (none). Do not write an EXTRACT block for dumps already filed.

Then add one block:
===CASEFILE===
and the full combined case file: every fact from the profile, the current case file, and every extraction, written as one readable file. Keep those facts. Do not invent facts, hosts, people, or numbers. No exploit steps, no attack procedures, no guessed vulnerabilities. If there is nothing to combine, write (none).`
}

async function refreshBrief() {
  const key = (state.settings.aiApiKey || "").trim()
  if (!key) {
    state.journal.summary = { ...state.journal.summary, status: "error", error: "Add an AI API key in Control." }
    save()
    if (state.settings.bSide) renderBoard()
    return
  }
  state.journal.summary = { ...state.journal.summary, status: "pending", error: "" }
  save()
  if (state.settings.bSide) renderBoard()
  const res = await ladenCall("aiChat", {
    provider: state.settings.aiProvider || "xai",
    apiKey: key,
    system: "You are Caleb, the Laden Ops journal helper. Write sharp authorized-engagement briefs. No exploit advice. Facts and structure only.",
    user: buildSummaryPrompt(state.journal),
  })
  if (res?.ok && res.text) {
    applyCaseWork(watchedScope(state.journal), res.text)
  } else {
    state.journal.summary = { ...state.journal.summary, status: "error", error: res?.error || "AI failed" }
  }
  save()
  if (state.settings.bSide) renderBoard()
}

async function ensureJournalWorkspace() {
  const id = state.journal.engagementId
  if (!id) {
    journalUi.termCwd = ""
    return
  }
  const res = await ladenCall("vaultEnsureDir", { rel: `engagements/${id}` })
  if (res?.ok && res.path) journalUi.termCwd = res.path
  else {
    journalUi.termCwd = ""
    pushTermLog(`workspace error: ${res?.error || "unknown"}`)
  }
  const cwd = document.querySelector(".journal-ws")
  if (cwd) cwd.textContent = journalUi.termCwd || ""
}

async function openScopedTerm(command, cwdOverride) {
  journalUi.termBusy = true
  renderBoard()
  try {
    let cwd = cwdOverride || journalUi.termCwd
    const id = state.journal.engagementId
    if (!cwdOverride && id) {
      const res = await ladenCall("vaultEnsureDir", { rel: `engagements/${id}` })
      if (res?.ok && res.path) {
        cwd = res.path
        journalUi.termCwd = res.path
        const eng = journalEngagements().find((n) => n.id === id)
        if (eng?.body) await ladenCall("vaultWriteNote", { rel: `engagements/${id}`, filename: "JOURNAL.md", body: eng.body })
      }
    }
    const res = await ladenCall("openTerminal", { cwd: cwd || "", command: command || "" })
    pushTermLog(res?.ok
      ? (command ? `term @ ${res.cwd || cwd || "~"} · ${command}` : `term open @ ${res.cwd || cwd || "~"}`)
      : `term failed: ${res?.error || "unknown"}`)
  } finally {
    journalUi.termBusy = false
    renderBoard()
  }
}

async function openScope(scope) {
  if (scope.kind === "case") {
    journalUi.monitorId = scope.id
    const pack = ensureScopeVault(scope)
    const res = await ladenCall("vaultEnsureDir", { rel: "engagements/" + pack.folder.id })
    if (res?.path) {
      const opened = await ladenCall("openPath", { path: res.path })
      pushTermLog(opened?.ok ? "case folder · " + scope.label : "case folder failed: " + (opened?.error || "unknown"))
    } else pushTermLog("case · " + scope.label)
    renderBoard()
    return
  }
  if (scope.kind === "browser") {
    await ladenCall("launch", { command: scope.value })
    pushTermLog(`browser · ${scope.label} → ${scope.value}`)
    renderBoard()
    return
  }
  await openScopedTerm("", scope.value)
}

async function ingestFiles(files) {
  for (const file of files) {
    if (!/\.(md|txt|log|json|csv)$/i.test(file.name) && file.type && !file.type.startsWith("text/")) continue
    const body = await file.text().catch(() => "")
    if (body.trim()) addDump(`# ${file.name}\n${body.slice(0, 20000)}`, "drop")
  }
}

function renderJournal() {
  const journal = state.journal
  const engagements = journalEngagements()
  const scopes = journal.scopes || []
  const monitored = scopes.filter((s) => s.monitoring)
  const active = monitored.find((s) => s.id === journalUi.monitorId) || monitored[0] || null
  const page = h("div", { class: "journal" })
  page.addEventListener("paste", (event) => {
    const tag = event.target && event.target.tagName
    if (tag === "TEXTAREA" || tag === "INPUT") return
    const text = event.clipboardData?.getData("text") || ""
    if (!text.trim()) return
    event.preventDefault()
    addDump(text, "paste")
  })

  const label = h("input", { value: journal.engagementLabel, placeholder: "Engagement label" })
  label.onchange = () => { journal.engagementLabel = label.value; save() }
  const link = dropdown(
    [{ value: "", label: "Link vault engagement…" }, ...engagements.map((eng) => ({ value: eng.id, label: eng.name }))],
    journal.engagementId || "",
  )
  link.onchange = () => {
    const eng = engagements.find((n) => n.id === link.value)
    journal.engagementId = link.value
    if (eng) journal.engagementLabel = eng.name
    save()
    void ensureJournalWorkspace().then(() => { if (state.settings.bSide) renderBoard() })
  }

  page.append(
    h("header", { class: "journal-head" }, [
      h("div", {}, [
        h("p", { class: "kicker" }, ["segment · 02 · caleb"]),
        sideMark(),
        h("p", { class: "journal-blurb" }, ["Dump, then organize, then a Caleb brief. The scoped terminal runs only the command you type. Caleb only summarizes the notebook."]),
      ]),
      flipChoice(),
    ]),
    h("div", { class: "inline journal-meta" }, [label, link]),
    h("div", { class: "journal-grid" }, [
      renderDump(journal),
      renderPortfolio(journal),
      renderScopes(journal, scopes, monitored, active),
      renderBrief(journal),
    ]),
  )
  if (journalUi.ctx) page.append(renderDumpMenu())
  if (journal.engagementId && !journalUi.termCwd) void ensureJournalWorkspace()
  return page
}

function renderDump(journal) {
  const box = h("section", { class: "journal-card-block" + (journalUi.dragOver ? " over" : "") })
  box.addEventListener("dragover", (event) => { event.preventDefault(); journalUi.dragOver = true; box.classList.add("over") })
  box.addEventListener("dragleave", () => { journalUi.dragOver = false; box.classList.remove("over") })
  box.addEventListener("drop", (event) => {
    event.preventDefault()
    journalUi.dragOver = false
    const text = event.dataTransfer?.getData("text/plain") || ""
    if (text.trim() && !event.dataTransfer?.files?.length) {
      addDump(text, "drop")
      return
    }
    if (event.dataTransfer?.files?.length) void ingestFiles([...event.dataTransfer.files])
  })
  const area = h("textarea", { rows: "3", placeholder: "Notes, URLs, or drop .md .txt .log here" })
  area.value = journalUi.draft
  area.oninput = () => { journalUi.draft = area.value }
  const head = h("div", { class: "journal-sec" }, [
    h("h3", {}, ["Dump"]),
    h("span", { class: "mono" }, [String(journal.dump.length)]),
  ])
  if (journal.dump.length) {
    head.append(h("button", { class: "btn btn-ghost", type: "button", onclick: () => { state.journal.dump = []; save(); renderBoard() } }, ["Clear"]))
  }
  box.append(head, h("p", { class: "hint" }, ["Paste anywhere on this side, drop a text file, or right-click a note to file it."]))
  box.append(h("div", { class: "inline" }, [
    area,
    h("button", { class: "btn btn-primary", type: "button", onclick: () => { if (!journalUi.draft.trim()) return; const text = journalUi.draft; journalUi.draft = ""; addDump(text, "manual") } }, ["Dump"]),
  ]))
  const list = h("div", { class: "journal-list" })
  if (!journal.dump.length) list.append(h("p", { class: "empty" }, ["Inbox empty."]))
  for (const item of journal.dump) {
    const file = dropdown(
      [{ value: "", label: "File into…" }, ...JOURNAL_BUCKETS.map(([id, label]) => ({ value: id, label }))],
      "",
    )
    file.onchange = () => { if (file.value) fileDump(item.id, file.value) }
    const row = h("div", { class: "dump-item" }, [
      h("span", { class: "mono muted" }, [item.source]),
      h("span", { class: "dump-raw" }, [item.raw]),
      file,
    ])
    row.addEventListener("contextmenu", (event) => {
      event.preventDefault()
      journalUi.ctx = { x: event.clientX, y: event.clientY, dumpId: item.id }
      renderBoard()
    })
    list.append(row)
  }
  box.append(list)
  return box
}

function renderPortfolio(journal) {
  const grid = h("div", { class: "bucket-grid" })
  for (const [id, label, hint] of JOURNAL_BUCKETS) {
    const cards = journal.cards.filter((c) => c.bucket === id)
    const col = h("div", { class: "bucket" }, [
      h("div", { class: "journal-sec" }, [h("h3", {}, [label]), h("span", { class: "mono" }, [String(cards.length)])]),
      h("p", { class: "hint" }, [hint]),
    ])
    for (const card of cards) {
      const move = dropdown(JOURNAL_BUCKETS.map(([bid, blabel]) => ({ value: bid, label: blabel })), card.bucket)
      move.onchange = () => {
        card.bucket = move.value
        card.updatedAt = new Date().toISOString()
        save()
        scheduleBrief()
        renderBoard()
      }
      col.append(h("article", { class: "j-card", title: card.body }, [
        h("div", { class: "vault-top" }, [
          h("strong", {}, [card.title]),
          h("button", { class: "btn btn-danger", type: "button", onclick: () => {
            state.journal.cards = state.journal.cards.filter((c) => c.id !== card.id)
            save(); scheduleBrief(); renderBoard()
          } }, ["×"]),
        ]),
        h("p", {}, [card.body.length > 140 ? card.body.slice(0, 140) + "…" : card.body]),
        move,
      ]))
    }
    grid.append(col)
  }
  return h("section", { class: "journal-card-block" }, [
    h("div", { class: "journal-sec" }, [h("h3", {}, ["Portfolio"])]),
    grid,
  ])
}

function renderScopes(journal, scopes, monitored, active) {
  const kind = dropdown(
    [{ value: "terminal", label: "terminal" }, { value: "browser", label: "browser" }],
    journalUi.scopeKind,
  )
  kind.onchange = () => { journalUi.scopeKind = kind.value; renderBoard() }
  const label = h("input", { placeholder: "Label" })
  label.value = journalUi.scopeLabel
  label.oninput = () => { journalUi.scopeLabel = label.value }
  const value = h("input", { placeholder: journalUi.scopeKind === "terminal" ? (journalUi.termCwd || "~/path") : "https://…", spellcheck: "false" })
  value.value = journalUi.scopeValue
  value.oninput = () => { journalUi.scopeValue = value.value }
  const list = h("div", { class: "scope-list" })
  if (!scopes.length) list.append(h("p", { class: "empty" }, ["No scopes yet."]))
  for (const scope of scopes) {
    const watch = switchButton(!!scope.monitoring)
    watch.title = scope.monitoring ? "On" : "Off"
    watch.onclick = () => {
      scope.monitoring = !scope.monitoring
      save(); scheduleBrief(); renderBoard()
    }
    const detail = scope.kind === "case"
      ? ((scope.facts || []).length + " profile")
      : scope.value
    list.append(h("div", { class: "scope-row" + (scope.monitoring ? " mon" : "") }, [
      watch,
      h("button", { class: "scope-main", type: "button", onclick: () => {
        journalUi.monitorId = scope.id
        if (scope.kind === "case") renderBoard()
        else void openScope(scope)
      } }, [
        h("span", { class: "mono" }, [scope.kind]),
        h("span", {}, [scope.label]),
        h("span", { class: "mono muted" }, [detail]),
      ]),
      h("button", { class: "btn btn-cyan", type: "button", title: "Print the case PDF", onclick: () => exportScopePdf(scope) }, ["PDF"]),
      h("button", { class: "btn btn-danger", type: "button", onclick: () => {
        state.journal.scopes = state.journal.scopes.filter((s) => s.id !== scope.id)
        save(); renderBoard()
      } }, ["×"]),
    ]))
  }
  const picker = dropdown(
    monitored.length
      ? monitored.map((scope) => ({ value: scope.id, label: scope.kind + " · " + scope.label }))
      : [{ value: "", label: "No monitored scopes" }],
    active?.id || "",
  )
  picker.disabled = !monitored.length
  picker.onchange = () => { journalUi.monitorId = picker.value; renderBoard() }
  const box = h("section", { class: "journal-card-block" }, [
    h("div", { class: "journal-sec" }, [h("h3", {}, ["Scopes"]), h("span", { class: "mono" }, [`${monitored.length}/${scopes.length}`])]),
    h("p", { class: "hint" }, ["Open a case to name the target, record what you already know, and file documents you already have. The engagement folder holds dump/, extracted/, casefile.md, and summary.md."]),
    h("button", { class: "btn btn-primary", type: "button", onclick: () => startCaseSetup() }, ["Open case"]),
    journalUi.caseSetup ? renderCaseSetup() : null,
    h("p", { class: "hint" }, ["A watch is a terminal or a page you already use. It also gets a folder in engagements."]),
    h("div", { class: "inline" }, [kind, label]),
    value,
    h("button", { class: "btn btn-primary", type: "button", onclick: () => {
      const created = addScope(journalUi.scopeKind, journalUi.scopeLabel, journalUi.scopeValue)
      if (!created) return
      journalUi.scopeLabel = ""
      journalUi.scopeValue = ""
    } }, ["Add scope"]),
  ])
  if (journalUi.termCwd) {
    box.append(h("button", { class: "btn btn-ghost", type: "button", onclick: () => {
      addScope("terminal", "engagement-ws", journalUi.termCwd)
    } }, ["+ engagement workspace"]))
    box.append(h("p", { class: "mono muted journal-ws" }, [journalUi.termCwd]))
  }
  box.append(list, h("div", { class: "journal-sec" }, [h("h3", {}, ["Monitor"]), picker]))
  if (active) {
    box.append(h("p", { class: "mono" }, [`watching · ${active.kind} · ${active.value}`]))
    const actions = h("div", { class: "inline" }, [
      h("button", { class: "btn btn-cyan", type: "button", onclick: () => openScope(active) }, [active.kind === "case" ? "Folder" : "Open"]),
    ])
    if (active.kind === "case" && (active.facts || []).length) {
      box.append(h("p", { class: "hint" }, [active.facts.map((fact) => fact.kind + " " + fact.value).join(" · ")]))
    }
    if (active.kind === "terminal") {
      actions.append(h("button", { class: "btn btn-ghost", type: "button", onclick: async () => {
        const res = await ladenCall("openPath", { path: active.value })
        pushTermLog(res?.ok ? `opened ${active.value}` : `open failed: ${res?.error || "unknown"}`)
        renderBoard()
      } }, ["Folder"]))
      const cmd = h("input", { placeholder: "Run in this terminal", spellcheck: "false" })
      cmd.value = journalUi.termCmd
      cmd.oninput = () => { journalUi.termCmd = cmd.value }
      actions.append(cmd, h("button", {
        class: "btn btn-primary",
        type: "button",
        onclick: () => {
          const command = journalUi.termCmd.trim()
          if (!command) return
          journalUi.termCmd = ""
          void openScopedTerm(command, active.value)
        },
      }, [journalUi.termBusy ? "Running…" : "Run"]))
    }
    box.append(actions)
  } else {
    box.append(h("p", { class: "empty" }, ["Turn a scope on to watch it here."]))
  }
  const log = h("div", { class: "term-log" })
  if (!journalUi.termLog.length) log.append(h("p", { class: "empty" }, ["Session log empty."]))
  for (const line of journalUi.termLog) {
    log.append(h("div", { class: "mono term-line" }, [
      h("span", { class: "muted" }, [new Date(line.at).toLocaleTimeString()]),
      h("span", {}, [" " + line.text]),
    ]))
  }
  box.append(log)
  return box
}

function renderBrief(journal) {
  const provider = dropdown([
    { value: "xai", label: "SpaceXAI · grok-4.7" },
    { value: "groq", label: "Groq" },
    { value: "openrouter", label: "OpenRouter" },
  ], state.settings.aiProvider || "xai")
  const key = h("input", { type: "password", placeholder: "API key", value: state.settings.aiApiKey || "" })
  const box = h("section", { class: "journal-card-block" }, [
    h("div", { class: "journal-sec" }, [
      h("h3", {}, ["Caleb's thoughts"]),
      h("span", { class: "mono sum-" + (journal.summary.status || "idle") }, [journal.summary.status || "idle"]),
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => { journalUi.showMotor = !journalUi.showMotor; renderBoard() } }, ["Motor"]),
      h("button", {
        class: "btn btn-cyan",
        type: "button",
        onclick: () => { if (state.journal.summary.status !== "pending") void refreshBrief() },
      }, [journal.summary.status === "pending" ? "Thinking…" : "Think"]),
      h("button", {
        class: "btn btn-ghost",
        type: "button",
        title: "Print the vault case PDF",
        onclick: () => {
          const scope = watchedScope(journal) || journal.scopes[0]
          if (!scope) { flashVault("Add a scope first"); return }
          void exportScopePdf(scope)
        },
      }, ["Export PDF"]),
    ]),
  ])
  if (!state.settings.aiApiKey) box.append(h("p", { class: "hint" }, ["Save an API key so Caleb can think."]))
  if (journalUi.showMotor || !state.settings.aiApiKey) {
    box.append(h("div", { class: "form" }, [
      provider,
      key,
      h("button", { class: "btn btn-primary", type: "button", onclick: () => {
        state.settings.aiProvider = provider.value
        state.settings.aiApiKey = key.value.trim()
        save()
        journalUi.showMotor = false
        renderBoard()
      } }, ["Save key"]),
    ]))
  }
  const live = watchedScope(journal)
  const livePack = live ? scopePack(live) : null
  const liveText = livePack?.summary?.body || journal.summary.text || ""
  if (journal.summary.error) box.append(h("p", { class: "err" }, [journal.summary.error]))
  box.append(h("pre", { class: "brief mono" }, [liveText || "Caleb's thoughts land here after notes exist and a key is saved."]))
  const extractedCount = livePack?.extracted
    ? (state.vaultFs || []).filter((n) => n.parentId === livePack.extracted.id && n.role === "extract").length
    : 0
  if (live) box.append(h("p", { class: "hint mono" }, ["summary.md · " + live.label + (extractedCount ? " · " + extractedCount + " extracted" : "")]))
  if (journal.summary.updatedAt) box.append(h("p", { class: "hint mono" }, ["stamped " + new Date(journal.summary.updatedAt).toLocaleString()]))
  return box
}

function renderDumpMenu() {
  const menu = h("div", { class: "journal-ctx", style: `left:${journalUi.ctx.x}px;top:${journalUi.ctx.y}px` })
  menu.addEventListener("click", (event) => event.stopPropagation())
  menu.append(h("p", { class: "mono muted" }, ["File dump"]))
  for (const [id, label] of JOURNAL_BUCKETS) {
    menu.append(h("button", { class: "journal-ctx-item", type: "button", onclick: () => fileDump(journalUi.ctx.dumpId, id) }, [label]))
  }
  menu.append(h("button", { class: "journal-ctx-item danger", type: "button", onclick: () => {
    state.journal.dump = state.journal.dump.filter((d) => d.id !== journalUi.ctx.dumpId)
    journalUi.ctx = null
    save(); renderBoard()
  } }, ["Delete"]))
  return menu
}

document.addEventListener("click", () => {
  if (!journalUi.ctx) return
  journalUi.ctx = null
  if (state.settings.bSide) renderBoard()
})
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && journalUi.ctx) {
    journalUi.ctx = null
    if (state.settings.bSide) renderBoard()
  }
})
