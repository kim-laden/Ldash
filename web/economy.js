const ECO_ACCOUNTS = [
  { code: "1920", name: "Bank", type: "asset" },
  { code: "1900", name: "Cash", type: "asset" },
  { code: "2400", name: "Card", type: "liability" },
  { code: "2000", name: "Equity", type: "equity" },
  { code: "3000", name: "Salary", type: "income" },
  { code: "3100", name: "Other income", type: "income" },
  { code: "5000", name: "Rent", type: "expense" },
  { code: "5100", name: "Food", type: "expense" },
  { code: "5200", name: "Transport", type: "expense" },
  { code: "5300", name: "Subscriptions", type: "expense" },
  { code: "5400", name: "Web & AI", type: "expense" },
  { code: "5500", name: "Music & video", type: "expense" },
  { code: "5900", name: "Other spend", type: "expense" },
]

const SUB_ACCOUNT = { web: "5400", ai: "5400", music: "5500", video: "5500", other: "5300" }

const economyUi = {
  draft: "",
  importText: "",
  dragOver: false,
  memo: "",
  amount: "",
  flow: "out",
  account: "5900",
  date: "",
  busy: false,
  pdfBusy: false,
  note: "",
}

function thisMonth() {
  const now = new Date()
  return now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0")
}

function round2(n) {
  const v = Number(n)
  if (!Number.isFinite(v)) return 0
  return Math.round((v + Number.EPSILON) * 100) / 100
}

function formatMoney(n, currency) {
  const v = round2(n)
  const sign = v < 0 ? "-" : ""
  const [whole, frac] = Math.abs(v).toFixed(2).split(".")
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, " ")
  const num = frac === "00" ? grouped : grouped + "." + frac
  return sign + num + " " + (currency || "NOK")
}

function parseAmount(raw) {
  let s = String(raw || "").trim().replace(/\s/g, "")
  if (!s) return null
  if (s.includes(",") && s.includes(".")) {
    if (s.lastIndexOf(",") > s.lastIndexOf(".")) s = s.replace(/\./g, "").replace(",", ".")
    else s = s.replace(/,/g, "")
  } else if (s.includes(",")) s = s.replace(",", ".")
  const n = Number(s)
  return Number.isFinite(n) ? round2(n) : null
}

function defaultEconomy() {
  return {
    currency: "NOK",
    month: thisMonth(),
    accounts: ECO_ACCOUNTS.map((a) => ({ ...a })),
    entries: [],
    budgets: [],
    dump: [],
    summary: { text: "", updatedAt: "", status: "idle", error: "" },
  }
}

function ensureEconomy(raw) {
  const base = defaultEconomy()
  if (!raw || typeof raw !== "object") return base
  const accounts = ECO_ACCOUNTS.map((a) => ({ ...a }))
  const seen = new Set(accounts.map((a) => a.code))
  for (const extra of Array.isArray(raw.accounts) ? raw.accounts : []) {
    const code = String(extra.code || "").replace(/\D/g, "").slice(0, 4)
    const type = ["asset", "liability", "equity", "income", "expense"].includes(extra.type) ? extra.type : ""
    if (!/^\d{4}$/.test(code) || !type || seen.has(code)) continue
    seen.add(code)
    accounts.push({ code, name: String(extra.name || code).slice(0, 32), type })
    if (accounts.length >= 24) break
  }
  const entries = []
  for (const entry of Array.isArray(raw.entries) ? raw.entries : []) {
    const clean = cleanEntry(entry)
    if (!clean || !clean.lines.every((line) => accounts.some((acc) => acc.code === line.code))) continue
    entries.push(clean)
    if (entries.length >= 2000) break
  }
  return {
    currency: String(raw.currency || "NOK").trim().toUpperCase().slice(0, 8) || "NOK",
    month: /^\d{4}-\d{2}$/.test(raw.month) ? raw.month : base.month,
    accounts,
    entries,
    budgets: (Array.isArray(raw.budgets) ? raw.budgets : []).slice(0, 400).map((row) => ({
      id: row.id || uid(),
      month: /^\d{4}-\d{2}$/.test(row.month) ? row.month : base.month,
      code: String(row.code || ""),
      amount: round2(row.amount),
    })).filter((row) => accounts.some((a) => a.code === row.code)),
    dump: (Array.isArray(raw.dump) ? raw.dump : []).slice(0, 80).map((item) => ({
      id: item.id || uid(),
      raw: String(item.raw || "").slice(0, 8000),
      source: String(item.source || "manual").slice(0, 16),
      at: item.at || new Date().toISOString(),
    })),
    summary: {
      text: raw.summary?.text || "",
      updatedAt: raw.summary?.updatedAt || "",
      status: raw.summary?.status || "idle",
      error: raw.summary?.error || "",
      budgetLines: budgetLinesOf(raw.summary?.budgetLines, accounts),
    },
  }
}

function cleanEntry(entry) {
  if (!entry || !Array.isArray(entry.lines) || entry.lines.length < 2) return null
  const lines = entry.lines.slice(0, 6).map((line) => ({
    code: String(line.code || ""),
    debit: round2(line.debit),
    credit: round2(line.credit),
  })).filter((line) => /^\d{4}$/.test(line.code) && (line.debit > 0 || line.credit > 0) && !(line.debit > 0 && line.credit > 0))
  if (lines.length < 2) return null
  const debit = round2(lines.reduce((sum, line) => sum + line.debit, 0))
  const credit = round2(lines.reduce((sum, line) => sum + line.credit, 0))
  if (debit <= 0 || debit !== credit) return null
  const date = /^\d{4}-\d{2}-\d{2}$/.test(entry.date) ? entry.date : ""
  if (!date) return null
  return {
    id: entry.id || uid(),
    date,
    memo: String(entry.memo || "").slice(0, 80),
    source: ["manual", "import", "dump", "sub"].includes(entry.source) ? entry.source : "manual",
    ref: String(entry.ref || "").slice(0, 80),
    lines,
  }
}

function budgetLinesOf(rows, accounts) {
  if (!Array.isArray(rows)) return []
  const out = []
  for (const row of rows) {
    const code = String(row.code || "")
    const amount = round2(row.amount)
    if (!accounts.some((acct) => acct.code === code) || amount < 0) continue
    out.push({ code, amount })
    if (out.length >= 24) break
  }
  return out
}

function accountByCode(code) {
  return (state.economy?.accounts || ECO_ACCOUNTS).find((a) => a.code === code) || null
}

function bookCurrency() {
  return (state.economy?.currency || "NOK").toUpperCase()
}

function redactMoneyText(text) {
  return String(text || "")
    .replace(/\b(sk-|xai-|gsk_|sk-or-)[A-Za-z0-9_\-]{8,}/g, "[redacted]")
    .replace(/\b(api[_-]?key|password|token|secret)\s*[:=]\s*\S+/gi, "$1: [redacted]")
}

function shiftMonthKey(month, delta) {
  const [y, m] = month.split("-").map(Number)
  const d = new Date(y, (m - 1) + delta, 1)
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0")
}

function syncSubEntries(books) {
  const month = books.month
  const cur = (books.currency || "NOK").toUpperCase()
  const wanted = new Map()
  for (const sub of state.subscriptions || []) {
    if (((sub.currency || "NOK").toUpperCase()) !== cur) continue
    const amount = round2(toMonthly(sub))
    if (!(amount > 0)) continue
    wanted.set("sub:" + sub.id + ":" + month, { sub, amount, code: SUB_ACCOUNT[sub.category] || "5300" })
  }
  let changed = false
  for (const [ref, item] of wanted) {
    const entry = books.entries.find((row) => row.ref === ref)
    const lines = [
      { code: item.code, debit: item.amount, credit: 0 },
      { code: "1920", debit: 0, credit: item.amount },
    ]
    if (!entry) {
      books.entries.push({
        id: uid(),
        date: month + "-01",
        memo: item.sub.name,
        source: "sub",
        ref,
        lines,
      })
      changed = true
      continue
    }
    const same = entry.memo === item.sub.name
      && entry.date === month + "-01"
      && entry.lines.length === 2
      && entry.lines[0].code === item.code
      && entry.lines[0].debit === item.amount
      && entry.lines[1].code === "1920"
      && entry.lines[1].credit === item.amount
    if (!same) {
      entry.memo = item.sub.name
      entry.date = month + "-01"
      entry.lines = lines
      changed = true
    }
  }
  const next = books.entries.filter((row) => row.source !== "sub" || !String(row.ref).endsWith(":" + month) || wanted.has(row.ref))
  if (next.length !== books.entries.length) {
    books.entries = next
    changed = true
  }
  if (changed) save()
  return changed
}

function monthMoves(books, code, month) {
  let openDebit = 0
  let openCredit = 0
  let debit = 0
  let credit = 0
  for (const entry of books.entries) {
    const key = String(entry.date || "").slice(0, 7)
    for (const line of entry.lines) {
      if (line.code !== code) continue
      if (key < month) {
        openDebit += line.debit
        openCredit += line.credit
      } else if (key === month) {
        debit += line.debit
        credit += line.credit
      }
    }
  }
  const acct = accountByCode(code)
  const creditNormal = acct && (acct.type === "income" || acct.type === "liability" || acct.type === "equity")
  const open = round2(creditNormal ? openCredit - openDebit : openDebit - openCredit)
  const close = round2(creditNormal ? open + credit - debit : open + debit - credit)
  return { open, debit: round2(debit), credit: round2(credit), close }
}

function monthFigures(books) {
  const month = books.month
  let inn = 0
  let out = 0
  let subs = 0
  for (const acct of books.accounts) {
    const move = monthMoves(books, acct.code, month)
    if (acct.type === "income") inn += move.credit - move.debit
    if (acct.type === "expense") out += move.debit - move.credit
  }
  for (const entry of books.entries) {
    if (entry.source !== "sub" || String(entry.date).slice(0, 7) !== month) continue
    subs += entry.lines.reduce((sum, line) => sum + line.debit, 0)
  }
  return { inn: round2(inn), out: round2(out), subs: round2(subs), left: round2(inn - out) }
}

function splitRow(line) {
  if (line.includes(";")) return line.split(";").map((part) => part.trim())
  if (line.includes(",")) return line.split(",").map((part) => part.trim())
  return [line.trim()]
}

function entryFromSigned(date, memo, amount, code, source) {
  const abs = round2(Math.abs(amount))
  if (!(abs > 0) || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null
  const named = accountByCode(code) ? code : ""
  const debit = amount < 0 ? (named || "5900") : "1920"
  const credit = amount < 0 ? "1920" : (named || "3100")
  if (debit === credit || !accountByCode(debit) || !accountByCode(credit)) return null
  return cleanEntry({
    id: uid(),
    date,
    memo,
    source,
    ref: "",
    lines: [
      { code: debit, debit: abs, credit: 0 },
      { code: credit, debit: 0, credit: abs },
    ],
  })
}

function parseMoneyLines(text, month, source) {
  const kind = source === "dump" ? "dump" : "import"
  const lines = String(text || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
  const entries = []
  let skipped = 0
  let start = 0
  let cols = null
  const head = (lines[0] || "").toLowerCase()
  if (/date/.test(head) && /amount|sum|bel[oø]p/.test(head)) {
    const parts = splitRow(lines[0]).map((part) => part.toLowerCase())
    cols = {
      date: parts.findIndex((part) => /date/.test(part)),
      memo: parts.findIndex((part) => /memo|desc|text|name|service/.test(part)),
      amount: parts.findIndex((part) => /amount|sum|belop|beløp/.test(part)),
      code: parts.findIndex((part) => /account|code|konto/.test(part)),
    }
    start = 1
  }
  for (const line of lines.slice(start)) {
    let entry = null
    if (cols) {
      const parts = splitRow(line)
      const date = parts[cols.date] || ""
      const memo = cols.memo >= 0 ? parts[cols.memo] : ""
      const amount = parseAmount(parts[cols.amount])
      const code = cols.code >= 0 ? parts[cols.code] : ""
      if (amount) entry = entryFromSigned(date, memo || "import", amount, code, kind)
    } else {
      const parts = splitRow(line)
      if (parts.length >= 3 && /^\d{4}-\d{2}-\d{2}$/.test(parts[0])) {
        const amount = parseAmount(parts[2])
        if (amount) entry = entryFromSigned(parts[0], parts[1] || "line", amount, parts[3] || "", kind)
      } else {
        const match = line.match(/^(.+?)\s+([+-]?\d[\d\s]*(?:[.,]\d{1,2})?)\s*(?:nok|usd|eur|kr)?$/i)
        if (match) {
          const amount = parseAmount(match[2])
          if (amount) entry = entryFromSigned(month + "-01", match[1].trim(), amount, "", kind)
        }
      }
    }
    if (entry) entries.push(entry)
    else skipped += 1
  }
  return { entries, skipped }
}

function sameEntry(a, b) {
  return a.date === b.date && a.memo === b.memo && a.lines[0].debit + a.lines[0].credit === b.lines[0].debit + b.lines[0].credit && a.lines[0].code === b.lines[0].code
}

function postEntries(entries) {
  const books = state.economy
  let added = 0
  for (const entry of entries) {
    if (books.entries.some((row) => sameEntry(row, entry))) continue
    books.entries.push(entry)
    added += 1
  }
  if (added) save()
  return added
}

function postMoneyText(text, month, source) {
  const parsed = parseMoneyLines(text, month || state.economy.month, source)
  const added = postEntries(parsed.entries)
  return { added, skipped: parsed.skipped, found: parsed.entries.length }
}

function addEcoDump(raw, source) {
  const text = redactMoneyText(raw).trim()
  if (!text) return
  state.economy.dump.unshift({ id: uid(), raw: text.slice(0, 8000), source: source || "manual", at: new Date().toISOString() })
  state.economy.dump = state.economy.dump.slice(0, 80)
  save()
  renderBoard()
}

function addEcoEntry(date, memo, flow, code, amount) {
  const abs = round2(Math.abs(parseAmount(amount)))
  if (!(abs > 0)) return null
  const flowName = flow === "in" || flow === "move" ? flow : "out"
  let debit = "1920"
  let credit = code
  if (flowName === "out") {
    debit = code
    credit = "1920"
  } else if (flowName === "move") {
    debit = code
    credit = "1900"
  }
  if (debit === credit || !accountByCode(debit) || !accountByCode(credit)) return null
  const entry = cleanEntry({
    date,
    memo,
    source: "manual",
    lines: [
      { code: debit, debit: abs, credit: 0 },
      { code: credit, debit: 0, credit: abs },
    ],
  })
  if (!entry) return null
  state.economy.entries.push(entry)
  save()
  return entry
}

function renderEconomy() {
  const books = state.economy
  syncSubEntries(books)
  const page = h("div", { class: "journal economy" })
  page.addEventListener("paste", (event) => {
    const tag = event.target && event.target.tagName
    if (tag === "TEXTAREA" || tag === "INPUT") return
    const text = event.clipboardData?.getData("text") || ""
    if (!text.trim()) return
    event.preventDefault()
    addEcoDump(text, "paste")
  })
  page.append(
    h("header", { class: "journal-head" }, [
      h("div", {}, [
        h("p", { class: "kicker" }, ["segment · 03 · caleb"]),
        sideMark(),
        h("p", { class: "journal-blurb" }, ["Dump pay and bills, import a statement, and the month stays in balance. Caleb drafts a budget only from figures you already dumped."]),
      ]),
      flipChoice(),
    ]),
    h("div", { class: "journal-grid" }, [
      h("div", { class: "eco-col" }, [renderEcoDump(books), renderEcoImport(books)]),
      h("div", { class: "eco-col" }, [renderEcoMonth(books), renderEcoCaleb(books)]),
    ]),
  )
  return page
}

function renderEcoDump(books) {
  const box = h("section", { class: "journal-card-block" + (economyUi.dragOver ? " over" : "") })
  box.addEventListener("dragover", (event) => { event.preventDefault(); economyUi.dragOver = true; box.classList.add("over") })
  box.addEventListener("dragleave", () => { economyUi.dragOver = false; box.classList.remove("over") })
  box.addEventListener("drop", (event) => {
    event.preventDefault()
    economyUi.dragOver = false
    const text = event.dataTransfer?.getData("text/plain") || ""
    if (text.trim() && !event.dataTransfer?.files?.length) {
      addEcoDump(text, "drop")
      return
    }
    if (event.dataTransfer?.files?.length) void ingestEcoFiles([...event.dataTransfer.files])
  })
  const area = h("textarea", { rows: "3", placeholder: "Payday, rent, a receipt, or drop .csv .txt" })
  area.value = economyUi.draft
  area.oninput = () => { economyUi.draft = area.value }
  const head = h("div", { class: "journal-sec" }, [
    h("h3", {}, ["Dump"]),
    h("span", { class: "mono" }, [String(books.dump.length)]),
  ])
  if (books.dump.length) {
    head.append(h("button", { class: "btn btn-ghost", type: "button", onclick: () => { state.economy.dump = []; save(); renderBoard() } }, ["Clear"]))
  }
  box.append(head, h("p", { class: "hint" }, ["The dump follows onto this side. Paste here or drop a text file. The journal keeps its own inbox."]))
  box.append(h("div", { class: "inline" }, [
    area,
    h("button", { class: "btn btn-primary", type: "button", onclick: () => {
      if (!economyUi.draft.trim()) return
      const text = economyUi.draft
      economyUi.draft = ""
      addEcoDump(text, "manual")
    } }, ["Dump"]),
  ]))
  box.append(h("button", { class: "btn btn-cyan", type: "button", onclick: () => {
    const blob = books.dump.map((item) => item.raw).join("\n")
    const result = postMoneyText(blob, books.month, "dump")
    economyUi.note = result.added
      ? "Posted " + result.added + " line" + (result.added === 1 ? "" : "s") + " from the dump."
      : "No readable money lines in the dump."
    renderBoard()
  } }, ["Post readable lines"]))
  const list = h("div", { class: "journal-list" })
  if (!books.dump.length) list.append(h("p", { class: "empty" }, ["Inbox empty."]))
  for (const item of books.dump) {
    list.append(h("div", { class: "dump-item" }, [
      h("span", { class: "mono muted" }, [item.source]),
      h("span", { class: "dump-raw" }, [item.raw]),
      h("button", { class: "btn btn-danger", type: "button", onclick: () => {
        state.economy.dump = state.economy.dump.filter((row) => row.id !== item.id)
        save(); renderBoard()
      } }, ["×"]),
    ]))
  }
  box.append(list)
  return box
}

async function ingestEcoFiles(files) {
  for (const file of files) {
    if (!/\.(md|txt|log|csv|tsv)$/i.test(file.name) && file.type && !file.type.startsWith("text/")) continue
    const body = await file.text().catch(() => "")
    if (!body.trim()) continue
    if (/\.(csv|tsv)$/i.test(file.name)) {
      const result = postMoneyText(body, state.economy.month)
      economyUi.note = file.name + " · posted " + result.added + ", skipped " + result.skipped
      addEcoDump("# " + file.name + "\n" + body.slice(0, 4000), "import")
    } else addEcoDump("# " + file.name + "\n" + body.slice(0, 20000), "drop")
  }
}

function renderEcoImport(books) {
  const flow = dropdown(
    [{ value: "out", label: "Money out" }, { value: "in", label: "Money in" }, { value: "move", label: "Move funds" }],
    economyUi.flow,
  )
  flow.onchange = () => { economyUi.flow = flow.value; renderBoard() }
  const choices = books.accounts.filter((acct) => {
    if (economyUi.flow === "in") return acct.type === "income"
    if (economyUi.flow === "move") return acct.type === "asset" || acct.type === "liability"
    return acct.type === "expense"
  })
  if (!choices.some((acct) => acct.code === economyUi.account)) economyUi.account = choices[0]?.code || "5900"
  const account = dropdown(choices.map((acct) => ({ value: acct.code, label: acct.code + " " + acct.name })), economyUi.account)
  account.onchange = () => { economyUi.account = account.value }
  const date = h("input", { type: "date", value: economyUi.date || (books.month + "-01") })
  date.onchange = () => { economyUi.date = date.value }
  const memo = h("input", { placeholder: "What it was" })
  memo.value = economyUi.memo
  memo.oninput = () => { economyUi.memo = memo.value }
  const amount = h("input", { placeholder: "Amount", inputmode: "decimal" })
  amount.value = economyUi.amount
  amount.oninput = () => { economyUi.amount = amount.value }
  const file = h("input", { type: "file", accept: ".csv,.tsv,.txt,text/csv,text/plain" })
  file.onchange = () => { if (file.files?.length) void ingestEcoFiles([...file.files]) }
  const paste = h("textarea", { rows: "3", placeholder: "2026-10-01, salary, 45000, 3000\nrent -14500" })
  paste.value = economyUi.importText
  paste.oninput = () => { economyUi.importText = paste.value }
  const box = h("section", { class: "journal-card-block" }, [
    h("div", { class: "journal-sec" }, [h("h3", {}, ["Post & import"])]),
    h("p", { class: "hint" }, ["Money out credits the bank (1920). Money in debits it. A negative amount in a file is money out. Lines that repeat are skipped."]),
    h("div", { class: "inline" }, [flow, account]),
    h("div", { class: "inline" }, [date, memo, amount]),
    h("button", { class: "btn btn-primary", type: "button", onclick: () => {
      const entry = addEcoEntry(date.value, economyUi.memo.trim() || "manual", economyUi.flow, economyUi.account, economyUi.amount)
      if (!entry) {
        economyUi.note = "Need a date, a real amount, and two different accounts."
        renderBoard()
        return
      }
      economyUi.memo = ""
      economyUi.amount = ""
      economyUi.note = "Posted " + entry.memo + "."
      renderBoard()
    } }, ["Post line"]),
    file,
    paste,
    h("button", { class: "btn btn-cyan", type: "button", onclick: () => {
      if (!economyUi.importText.trim()) return
      const result = postMoneyText(economyUi.importText, books.month)
      economyUi.importText = ""
      economyUi.note = "Imported " + result.added + ". Skipped " + result.skipped + "."
      renderBoard()
    } }, ["Import lines"]),
  ])
  if (economyUi.note) box.append(h("p", { class: "hint mono" }, [economyUi.note]))
  box.append(renderEcoLines(books))
  return box
}

function renderEcoLines(books) {
  const rows = books.entries
    .filter((entry) => String(entry.date).slice(0, 7) === books.month)
    .slice()
    .sort((a, b) => a.date.localeCompare(b.date) || a.memo.localeCompare(b.memo))
  const list = h("div", { class: "journal-list" })
  list.append(h("div", { class: "journal-sec" }, [h("h3", {}, ["Journal"]), h("span", { class: "mono" }, [String(rows.length)])]))
  if (!rows.length) list.append(h("p", { class: "empty" }, ["Nothing posted in " + books.month + " yet."]))
  for (const entry of rows) {
    const debit = entry.lines.find((line) => line.debit > 0)
    const credit = entry.lines.find((line) => line.credit > 0)
    const amount = debit ? debit.debit : 0
    const kids = [
      h("span", { class: "mono" }, [entry.date.slice(8)]),
      h("span", { class: "dump-raw" }, [entry.memo]),
      h("span", { class: "mono muted" }, ["Dr " + (debit?.code || "—") + " Cr " + (credit?.code || "—")]),
      h("span", { class: "mono" }, [formatMoney(amount, bookCurrency())]),
      h("span", { class: "mono muted" }, [entry.source]),
    ]
    if (entry.source !== "sub") {
      kids.push(h("button", { class: "btn btn-danger", type: "button", onclick: () => {
        state.economy.entries = state.economy.entries.filter((row) => row.id !== entry.id)
        save(); renderBoard()
      } }, ["×"]))
    }
    list.append(h("div", { class: "dump-item" }, kids))
  }
  return list
}

function renderEcoMonth(books) {
  const figures = monthFigures(books)
  const cur = bookCurrency()
  const prev = h("button", { class: "btn btn-ghost", type: "button", onclick: () => { books.month = shiftMonthKey(books.month, -1); save(); renderBoard() } }, ["‹"])
  const next = h("button", { class: "btn btn-ghost", type: "button", onclick: () => { books.month = shiftMonthKey(books.month, 1); save(); renderBoard() } }, ["›"])
  const box = h("section", { class: "journal-card-block" }, [
    h("div", { class: "journal-sec" }, [
      h("h3", {}, ["Monthly account"]),
      h("span", { class: "mono" }, [books.month + " · " + cur]),
    ]),
    h("div", { class: "inline eco-month" }, [prev, h("strong", { class: "mono" }, [books.month]), next]),
    h("div", { class: "eco-figures" }, [
      figure("In", figures.inn, cur, "in"),
      figure("Out", figures.out, cur, "out"),
      figure("Subs", figures.subs, cur, "subs"),
      figure("Left", figures.left, cur, figures.left < 0 ? "out" : "in"),
    ]),
    h("p", { class: "hint" }, ["Subscriptions post to the bank on the first of the month. Dump payday and the other bills so the month can close."]),
  ])
  const foreign = (state.subscriptions || []).filter((sub) => (sub.currency || "NOK").toUpperCase() !== cur)
  if (foreign.length) {
    box.append(h("p", { class: "hint" }, [foreign.map((sub) => sub.name).join(", ") + " use another currency, so they stay on the subs panel and off this account."]))
  }
  box.append(renderGl(books))
  return box
}

function figure(label, amount, currency, tone) {
  return h("div", { class: "eco-fig " + tone }, [
    h("span", {}, [label]),
    h("strong", {}, [formatMoney(amount, currency)]),
  ])
}

function renderGl(books) {
  const table = h("div", { class: "gl" })
  table.append(h("div", { class: "gl-row head" }, [
    h("span", {}, ["Code"]),
    h("span", {}, ["Account"]),
    h("span", {}, ["Debit"]),
    h("span", {}, ["Credit"]),
    h("span", {}, ["Balance"]),
    h("span", {}, ["Budget"]),
  ]))
  let shown = 0
  for (const acct of books.accounts) {
    const move = monthMoves(books, acct.code, books.month)
    const budget = books.budgets.find((row) => row.month === books.month && row.code === acct.code)
    if (!move.debit && !move.credit && !move.open && !budget) continue
    shown += 1
    table.append(h("div", { class: "gl-row" }, [
      h("span", { class: "mono" }, [acct.code]),
      h("span", {}, [acct.name]),
      h("span", { class: "mono" }, [move.debit ? formatMoney(move.debit, "") : "—"]),
      h("span", { class: "mono" }, [move.credit ? formatMoney(move.credit, "") : "—"]),
      h("span", { class: "mono" }, [formatMoney(move.close, "")]),
      h("span", { class: "mono" }, [budget ? formatMoney(budget.amount, "") : "—"]),
    ]))
  }
  if (!shown) table.append(h("p", { class: "empty" }, ["No movement this month."]))
  return table
}

function renderEcoCaleb(books) {
  const box = h("section", { class: "journal-card-block" }, [
    h("div", { class: "journal-sec" }, [
      h("h3", {}, ["Caleb's month"]),
      h("span", { class: "mono sum-" + (books.summary.status || "idle") }, [books.summary.status || "idle"]),
      h("button", { class: "btn btn-cyan", type: "button", onclick: () => { if (!economyUi.busy) void thinkEconomy() } }, [economyUi.busy ? "Thinking…" : "Think"]),
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => applyCalebBudget() }, ["Apply budget"]),
      h("button", { class: "btn btn-primary", type: "button", onclick: () => { void printBudget() } }, [economyUi.pdfBusy ? "Printing…" : "Print budget"]),
    ]),
    h("p", { class: "hint" }, ["Caleb reads the dump, the subscriptions, and this month's ledger. He will not invent a figure that is not already there."]),
  ])
  if (!state.settings.aiApiKey) box.append(h("p", { class: "hint" }, ["Save Caleb's key on the journal, or in Control. The same key reads the month."]))
  if (books.summary.error) box.append(h("p", { class: "err" }, [books.summary.error]))
  box.append(h("pre", { class: "brief mono" }, [books.summary.text || "Dump a few lines, then ask Caleb for the month."]))
  if (books.summary.updatedAt) box.append(h("p", { class: "hint mono" }, ["stamped " + new Date(books.summary.updatedAt).toLocaleString()]))
  return box
}

function buildEconomyPrompt(books) {
  const cur = bookCurrency()
  const figures = monthFigures(books)
  const subs = (state.subscriptions || [])
    .filter((sub) => (sub.currency || "NOK").toUpperCase() === cur)
    .map((sub) => "- " + sub.name + " · " + (sub.category || "other") + " · " + formatMoney(toMonthly(sub), cur) + " /mnd")
    .join("\n") || "(none)"
  const ledger = books.accounts.map((acct) => {
    const move = monthMoves(books, acct.code, books.month)
    if (!move.debit && !move.credit) return ""
    return "- " + acct.code + " " + acct.name + " debit " + move.debit + " credit " + move.credit + " balance " + move.close
  }).filter(Boolean).join("\n") || "(no movement)"
  const budget = books.budgets.filter((row) => row.month === books.month)
    .map((row) => "- " + row.code + " " + row.amount)
    .join("\n") || "(none)"
  const dump = books.dump.slice(0, 30).map((item, i) => "[dump " + (i + 1) + "] " + redactMoneyText(item.raw).slice(0, 500)).join("\n") || "(empty)"
  const codes = books.accounts.map((acct) => acct.code + " " + acct.name).join(", ")
  return `You are Caleb, the monthly bookkeeper in Laden Ops. Use only figures that appear in the subscriptions, the ledger, or the dump. Never invent income, expenses, balances, or budgets. Never give tax, legal, or investment advice. If a password or API key appears, ignore it.

Month: ${books.month}
Currency: ${cur}
Known totals: in ${figures.inn}, out ${figures.out}, subscriptions ${figures.subs}, left ${figures.left}

=== SUBSCRIPTIONS (already per month) ===
${subs}

=== LEDGER THIS MONTH ===
${ledger}

=== BUDGET ALREADY SET ===
${budget}

=== DUMP ===
${dump}

Write "Caleb's month" with these short sections:
1) MONTH — one line, in against out
2) IN — income actually present
3) OUT — spending, including subscriptions
4) SUBS — the monthly subscription total
5) ROOM — what is left from the known figures
6) NEXT — up to 3 missing pieces to dump, with no made-up amounts

Max 180 words. Short lines. No tables.

Then a line ===BUDGET===
Then one line per account you can support with a number already present, exactly: code amount
Allowed codes: ${codes}
The amount is that account's plan for this month. Skip anything you cannot support. If none, write (none).
Then a line ===NOTES=== and stop.`
}

function splitEconomyBrief(text) {
  const raw = String(text || "")
  const budgetAt = raw.indexOf("===BUDGET===")
  const summary = (budgetAt >= 0 ? raw.slice(0, budgetAt) : raw).trim()
  return { summary, budget: parseBudgetBlock(raw) }
}

function parseBudgetBlock(text) {
  const start = String(text || "").indexOf("===BUDGET===")
  if (start < 0) return []
  const body = String(text).slice(start + 12).split("===NOTES===")[0]
  const rows = []
  for (const line of body.split("\n")) {
    const match = line.trim().match(/^(\d{4})\s+([+-]?\d[\d\s]*(?:[.,]\d{1,2})?)$/)
    if (!match || !accountByCode(match[1])) continue
    const amount = parseAmount(match[2])
    if (amount == null || amount < 0) continue
    rows.push({ code: match[1], amount })
  }
  return rows
}

async function thinkEconomy() {
  const key = (state.settings.aiApiKey || "").trim()
  const books = state.economy
  if (!key) {
    books.summary = { ...books.summary, status: "error", error: "Add an API key so Caleb can read the month." }
    save(); renderBoard(); return
  }
  economyUi.busy = true
  books.summary = { ...books.summary, status: "pending", error: "" }
  save(); renderBoard()
  const res = await ladenCall("aiChat", {
    provider: state.settings.aiProvider || "xai",
    apiKey: key,
    system: "You are Caleb, Laden Ops' monthly bookkeeper. Facts from the books and the dump only. No invented amounts.",
    user: buildEconomyPrompt(books),
  })
  economyUi.busy = false
  if (res?.ok && res.text) {
    const parts = splitEconomyBrief(res.text)
    books.summary = { text: parts.summary, budgetLines: parts.budget, updatedAt: new Date().toISOString(), status: "ok", error: "" }
  } else {
    books.summary = { ...books.summary, status: "error", error: res?.error || "Caleb could not read the month." }
  }
  save(); renderBoard()
}

function applyCalebBudget() {
  const books = state.economy
  const rows = Array.isArray(books.summary.budgetLines) && books.summary.budgetLines.length
    ? books.summary.budgetLines
    : parseBudgetBlock(books.summary.text || "")
  if (!rows.length) {
    economyUi.note = "Caleb has no budget lines to apply yet."
    renderBoard()
    return
  }
  const codes = new Set(rows.map((row) => row.code))
  books.budgets = books.budgets.filter((row) => row.month !== books.month || !codes.has(row.code))
  for (const row of rows) {
    books.budgets.push({ id: uid(), month: books.month, code: row.code, amount: row.amount })
  }
  economyUi.note = "Applied " + rows.length + " budget line" + (rows.length === 1 ? "" : "s") + " for " + books.month + "."
  save(); renderBoard()
}

async function printBudget() {
  if (economyUi.pdfBusy) return
  const books = state.economy
  const cur = bookCurrency()
  const figures = monthFigures(books)
  economyUi.pdfBusy = true
  renderBoard()
  const subs = (state.subscriptions || []).map((sub) => sub.name + " · " + (sub.category || "other") + " · " + formatMoney(toMonthly(sub), sub.currency || cur) + " /mnd").join("\n") || "(none)"
  const gl = books.accounts.map((acct) => {
    const move = monthMoves(books, acct.code, books.month)
    const budget = books.budgets.find((row) => row.month === books.month && row.code === acct.code)
    if (!move.debit && !move.credit && !budget) return ""
    return acct.code + " " + acct.name + "  dr " + move.debit + "  cr " + move.credit + "  bal " + move.close + (budget ? "  budget " + budget.amount : "")
  }).filter(Boolean).join("\n") || "(no movement)"
  const sections = [
    { heading: "MONTH", body: books.month + " " + cur + "\nIn " + formatMoney(figures.inn, cur) + "\nOut " + formatMoney(figures.out, cur) + "\nSubs " + formatMoney(figures.subs, cur) + "\nLeft " + formatMoney(figures.left, cur) },
    { heading: "SUBSCRIPTIONS", body: subs },
    { heading: "MONTHLY ACCOUNT", body: gl },
    { heading: "CALEB", body: redactMoneyText(books.summary.text) || "(no brief yet)" },
    { heading: "DUMP", body: books.dump.slice(0, 20).map((item) => redactMoneyText(item.raw).slice(0, 500)).join("\n\n") || "(empty)" },
  ]
  const res = await ladenCall("exportBudgetPdf", {
    rel: "economy/" + books.month,
    title: "Budget " + books.month,
    sections,
  })
  economyUi.pdfBusy = false
  economyUi.note = res?.ok ? "Budget printed." : (res?.error || "Could not print the budget.")
  renderBoard()
}
