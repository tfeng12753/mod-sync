// DEV-ONLY in-browser mock of the backend API (docs/api-contract.md).
// Enabled with VITE_USE_MOCK=1; never loaded otherwise. All data is SYNTHETIC.
// It is a rough keyword imitation so the UI can be built and screenshotted without the backend;
// the real parsing, feasibility and ranking live in backend/. Trigger phrases are listed in README.md.
import MENU from '../../fixtures/menu.json'

const HOLD_MS = 10 * 60 * 1000

// Synthetic stock, same ids/names as backend/data/stock.json (available = on_hand - safety buffer).
const INITIAL_STOCK = [
  ['rice', 'Jasmine rice', 'g', 8000, 0], ['mixed_greens', 'Mixed greens', 'g', 1500, 0],
  ['chicken', 'Chicken thigh', 'g', 3000, 150], ['tofu', 'Firm tofu', 'g', 900, 150],
  ['mushrooms', 'Cremini mushrooms', 'g', 2500, 0], ['avocado', 'Avocado', 'g', 1200, 60],
  ['wheat_noodles', 'Wheat noodles', 'g', 3000, 0], ['peanuts', 'Roasted peanuts', 'g', 500, 0],
  ['scallion', 'Scallion', 'g', 400, 0], ['flour_tortilla', 'Flour tortilla', 'each', 30, 0],
  ['lettuce', 'Romaine lettuce', 'g', 1500, 0], ['tomato', 'Tomato', 'g', 1500, 0],
  ['garlic_sauce', 'Garlic sauce', 'ml', 1200, 0], ['sesame_dressing', 'Sesame dressing', 'ml', 800, 0],
  ['peanut_sauce', 'Peanut sauce', 'ml', 900, 0], ['tahini_sauce', 'Tahini sauce', 'ml', 400, 0],
  ['falafel', 'Falafel', 'each', 60, 4], ['miso_broth', 'Miso broth', 'ml', 3500, 0],
  ['pickled_onion', 'Pickled red onion', 'g', 600, 0],
]

// component id -> [stock id, amount per serving, allergens, cross-contact]
const COMPONENTS = {
  rice_base: ['rice', 200, []], greens_base: ['mixed_greens', 80, []],
  noodles_base: ['wheat_noodles', 180, ['wheat'], 'Cooked in a shared wok also used for peanut sauce dishes.'],
  grilled_chicken: ['chicken', 150, []], crispy_tofu: ['tofu', 150, ['soy', 'wheat'], 'Fried in the shared fryer (also used for falafel).'],
  roasted_mushrooms: ['mushrooms', 150, []], tofu_cubes: ['tofu', 60, ['soy']],
  sliced_avocado: ['avocado', 60, []], crushed_peanuts: ['peanuts', 15, ['peanut']],
  scallions: ['scallion', 10, []], tortilla: ['flour_tortilla', 1, ['wheat']],
  lettuce: ['lettuce', 30, []], tomato: ['tomato', 40, []],
  garlic_sauce: ['garlic_sauce', 40, ['egg']], sesame_dressing: ['sesame_dressing', 40, ['sesame', 'soy', 'wheat']],
  peanut_sauce: ['peanut_sauce', 60, ['peanut', 'soy', 'wheat']], tahini_sauce: ['tahini_sauce', 40, ['sesame']],
  falafel: ['falafel', 4, ['wheat'], 'Fried in the shared fryer (also used for crispy tofu).'],
  miso_broth: ['miso_broth', 350, ['fish', 'soy']], pickled_onion: ['pickled_onion', 20, []],
}
const BASES = ['rice_base', 'noodles_base', 'tortilla', 'miso_broth', 'falafel']
const MEAT = ['grilled_chicken']
const ANIMAL = ['grilled_chicken', 'garlic_sauce', 'miso_broth']
const SIMILARITY = { crispy_tofu: 0.8, roasted_mushrooms: 0.65, sesame_dressing: 0.6, garlic_sauce: 0.5, greens_base: 0.7 }
const FORECAST_NOTES = {
  roasted_mushrooms: 'Forecast: mushrooms are in surplus this week, using them reduces waste.',
  crispy_tofu: 'Forecast: tofu is running low for tonight\'s expected demand.',
  sesame_dressing: 'Closest flavour profile among chef-approved sauces.',
  garlic_sauce: 'Plenty in stock; different flavour profile.',
  greens_base: 'Lighter base; plenty in stock.',
}
const ALLERGY_PROTOCOL = [
  'Wash hands and change gloves before starting this order.',
  'Use a clean cutting board, fresh utensils, and a clean pan or wok.',
  'Do not use shared fryer oil or shared garnish containers.',
  'Pack and seal this order separately and label it with the allergy.',
]
const ALLERGEN_WORDS = { peanut: 'peanut', sesame: 'sesame', soy: 'soy', wheat: 'wheat', gluten: 'wheat', egg: 'egg', fish: 'fish', dairy: 'milk', milk: 'milk' }

let stock = []
let orders = []
let nextOrder = 1

function resetState(seed) {
  stock = INITIAL_STOCK.map(([id, name, unit, onHand, buffer]) => ({ id, name, unit, on_hand: onHand, buffer }))
  orders = []
  nextOrder = 1
  if (seed) {
    const e = evaluate({ dish_id: 'satay_noodles', qty: 1, text: 'satay noodles but no peanuts at all, I have a severe peanut allergy', channel: 'doordash', customer_name: 'Sam' })
    createOrder(e, { channel: 'doordash', customer_name: 'Sam' })
    const e2 = evaluate({ dish_id: 'chicken_bowl', qty: 2, text: 'no pickled onion, sauce on the side', channel: 'walk-in', customer_name: 'Lee' })
    const o2 = createOrder(e2, { channel: 'walk-in', customer_name: 'Lee' })
    acceptOrder(o2, null)
  }
}

const iso = (ms) => new Date(ms).toISOString()
const clone = (x) => JSON.parse(JSON.stringify(x))
const stockItem = (id) => stock.find((s) => s.id === id)
const available = (s) => Math.max(0, s.on_hand - s.buffer)
const publicStock = (s) => ({
  id: s.id, name: s.name, unit: s.unit, on_hand: s.on_hand, available: available(s),
  initial_on_hand: INITIAL_STOCK.find(([id]) => id === s.id)?.[3] ?? s.on_hand,
})

function nameOf(dish, id) {
  const all = [...dish.components, ...dish.extras, ...dish.swaps.map((s) => ({ id: s.with, name: s.with_name }))]
  return all.find((c) => c.id === id)?.name ?? id.replace(/_/g, ' ')
}

function inStock(componentId, qty) {
  const c = COMPONENTS[componentId]
  if (!c) return true
  const s = stockItem(c[0])
  return s ? available(s) >= c[1] * qty : true
}

// Very small keyword "parser": finds the component a phrase refers to.
function findComponent(dish, phrase, pool = dish.components) {
  const p = phrase.trim()
  if (!p) return null
  // Score: full name > exact word > word stem (so "peanuts" picks Crushed peanuts over Peanut sauce).
  let best = null
  let bestScore = 0
  for (const c of pool) {
    const name = c.name.toLowerCase()
    let score = p.includes(name) ? 10 : 0
    for (const w of name.split(/\s+/)) {
      if (w.length <= 3) continue
      if (new RegExp(`\\b${w}\\b`).test(p)) score += 2
      else if (p.includes(w.replace(/s$/, ''))) score += 1
    }
    if (score > bestScore) { best = c; bestScore = score }
  }
  return best
}

function parse(dish, text) {
  const t = text.toLowerCase()
  const ops = []
  const restrictions = []
  const clauses = t.split(/[,.;!]| and | but | also /)

  for (const raw of clauses) {
    const c = raw.trim()
    let m
    if ((m = c.match(/(?:swap|sub|substitute|replace)\s+(?:the\s+)?(.+?)\s+(?:for|with)\s+(.+)/))) {
      const from = findComponent(dish, m[1])
      const to = findComponent(dish, m[2], dish.swaps.map((s) => ({ id: s.with, name: s.with_name })))
      if (from) ops.push({ op: 'substitute', ingredient: from.id, with: to?.id ?? `unknown:${m[2].trim()}` })
    } else if ((m = c.match(/(?:swap|sub|substitute|replace|instead of)\s+(?:the\s+)?(.+)/))) {
      const from = findComponent(dish, m[1])
      if (from) ops.push({ op: 'substitute', ingredient: from.id, with: null })
    } else if ((m = c.match(/(?:no|without|hold the|leave off)\s+(.+)/))) {
      const comp = findComponent(dish, m[1])
      if (comp) ops.push({ op: 'remove', ingredient: comp.id })
    } else if ((m = c.match(/(?:extra|double|add)\s+(.+)/))) {
      const ex = findComponent(dish, m[1], dish.extras)
      if (ex) ops.push({ op: 'add', ingredient: ex.id, qty: 1 })
    } else if ((m = c.match(/(.+?)\s+on the side/))) {
      const comp = findComponent(dish, m[1].replace(/^\s*(the)\s+/, ''), dish.components) ??
        (m[1].includes('sauce') || m[1].includes('dressing') ? dish.components.find((x) => /sauce|dressing/i.test(x.name)) : null)
      if (comp) ops.push({ op: 'on_side', ingredient: comp.id })
    } else if ((m = c.match(/(?:light|less|easy on the|easy on)\s+(.+)/))) {
      const comp = findComponent(dish, m[1])
      if (comp) ops.push({ op: 'less', ingredient: comp.id })
    }
  }

  if (/allerg|intoleran|celiac|anaphyla/.test(t)) {
    for (const [word, allergen] of Object.entries(ALLERGEN_WORDS)) {
      if (t.includes(word) && !restrictions.some((r) => r.value === allergen)) restrictions.push({ kind: 'allergy', value: allergen })
    }
  }
  if (/\bvegan\b/.test(t)) restrictions.push({ kind: 'diet', value: 'vegan' })
  else if (/vegetarian|no meat/.test(t)) restrictions.push({ kind: 'diet', value: 'vegetarian' })
  return { ops, restrictions }
}

function conflicts(componentId, restrictions) {
  const allergens = COMPONENTS[componentId]?.[2] ?? []
  const out = []
  for (const r of restrictions) {
    if (r.kind === 'allergy' && allergens.includes(r.value)) out.push(r.value)
    if (r.kind === 'diet' && r.value === 'vegetarian' && MEAT.includes(componentId)) out.push('not vegetarian')
    if (r.kind === 'diet' && r.value === 'vegan' && ANIMAL.includes(componentId)) out.push('not vegan')
  }
  return out
}

function instructionsFor(dish, ops, changes, restrictions) {
  const lines = []
  for (const r of restrictions) lines.push(r.kind === 'allergy' ? `ALLERGY: ${r.value}` : `DIET: ${r.value}`)
  if (restrictions.some((r) => r.kind === 'allergy')) lines.push(...ALLERGY_PROTOCOL)
  for (const o of ops) {
    if (changes.some((c) => c.replace === o.ingredient)) continue
    const n = nameOf(dish, o.ingredient)
    if (o.op === 'remove') lines.push(`NO ${n}`)
    if (o.op === 'add') lines.push(`EXTRA ${n}`)
    if (o.op === 'on_side') lines.push(`${n} ON THE SIDE`)
    if (o.op === 'less') lines.push(`LIGHT ${n}`)
    if (o.op === 'substitute' && o.with && !o.with.startsWith('unknown:')) lines.push(`SUB ${nameOf(dish, o.with)} for ${n}`)
  }
  for (const c of changes) lines.push(c.with ? `SUB ${c.with_name} for ${c.replace_name}` : `NO ${c.replace_name}`)
  return lines
}

function extrasPrice(dish, ops) {
  return ops.filter((o) => o.op === 'add').reduce((sum, o) => sum + (dish.extras.find((e) => e.id === o.ingredient)?.price_cents ?? 0) * (o.qty ?? 1), 0)
}

function finalComponents(dish, ops, changes) {
  const removed = new Set(ops.filter((o) => o.op === 'remove').map((o) => o.ingredient))
  let list = dish.components.map((c) => c.id).filter((id) => !removed.has(id))
  for (const ch of changes) list = ch.with ? list.map((id) => (id === ch.replace ? ch.with : id)) : list.filter((id) => id !== ch.replace)
  for (const o of ops) {
    if (o.op !== 'substitute' || !o.with || !COMPONENTS[o.with] || changes.some((c) => c.replace === o.ingredient)) continue
    list = list.map((id) => (id === o.ingredient ? o.with : id))
  }
  return list
}

function evidence(dish, comps, restrictions) {
  const allergensPresent = {}
  const cross = []
  const components = comps.map((id) => {
    const [, , allergens = [], cc] = COMPONENTS[id] ?? []
    const name = nameOf(dish, id)
    for (const a of allergens) (allergensPresent[a] ??= []).push(`${name}`)
    if (cc) cross.push(`${name}: ${cc}`)
    return { id, name, allergens, cross_contact: cc ? [cc] : [] }
  })
  return {
    components,
    allergens_present: allergensPresent,
    cross_contact: cross,
    excluded_allergens: restrictions.filter((r) => r.kind === 'allergy').map((r) => r.value),
    required_diet: restrictions.filter((r) => r.kind === 'diet').map((r) => r.value),
    note: 'Checked against listed ingredients only. Cross-contact is not verified by the system; staff must confirm before this is approved.',
  }
}

// Build alternative options that replace the given "problem" components.
function buildOptions(dish, problems, ops, restrictions, qty) {
  const route = restrictions.length ? 'staff' : 'customer'
  const perProblem = problems.map((p) => {
    const choices = []
    const unavailable = []
    for (const s of dish.swaps.filter((s) => s.replace === p.id)) {
      const bad = conflicts(s.with, restrictions)
      if (bad.length) unavailable.push({ label: `${s.with_name} instead of ${s.replace_name}`, reasons: [`${s.with_name}: ${bad.join(', ')}`] })
      else if (!inStock(s.with, qty)) unavailable.push({ label: `${s.with_name} instead of ${s.replace_name}`, reasons: [`${s.with_name} is out of stock.`] })
      else choices.push({ change: { replace: s.replace, replace_name: s.replace_name, with: s.with, with_name: s.with_name }, delta: s.price_delta_cents, sim: SIMILARITY[s.with] ?? 0.6 })
    }
    if (!BASES.includes(p.id)) choices.push({ change: { replace: p.id, replace_name: p.name, with: null, with_name: null }, delta: 0, sim: null })
    return { choices, unavailable }
  })
  const unavailable = perProblem.flatMap((p) => p.unavailable)
  if (perProblem.some((p) => p.choices.length === 0)) return { options: [], unavailable }

  // Combine: one option per choice of the first problem; other problems use their best choice.
  const first = perProblem[0].choices
  const rest = perProblem.slice(1).map((p) => p.choices[0])
  const options = first.map((choice, i) => {
    const picks = [choice, ...rest]
    const changes = picks.map((p) => p.change)
    const sims = picks.map((p) => p.sim).filter((s) => s != null)
    const similarity = sims.length ? Math.min(...sims) : null
    const score = similarity == null ? null : Math.round((0.55 * similarity + 0.35 * 0.8 + 0.1 * 0.7) * 100) / 100
    const reasons = picks.map((p) => (p.change.with ? FORECAST_NOTES[p.change.with] ?? 'Chef-approved swap.' : `Leave off ${p.change.replace_name}; no replacement.`))
    return {
      option_id: `opt-${i + 1}`,
      label: changes.map((c) => (c.with ? `${c.with_name} instead of ${c.replace_name}` : `Leave off ${c.replace_name}`)).join('; '),
      changes,
      route,
      score,
      similarity,
      price_change_cents: (picks.reduce((s, p) => s + p.delta, 0) + extrasPrice(dish, ops)) * qty,
      reasons,
      staff_notes: restrictions.length ? ['Verify cross-contact for every component before approving.'] : [],
      kitchen_instructions: instructionsFor(dish, ops, changes, restrictions),
    }
  })
  options.sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
  options.forEach((o, i) => (o.option_id = `opt-${i + 1}`))
  return { options, unavailable }
}

function evaluate(body) {
  const dish = MENU.find((d) => d.id === body.dish_id)
  if (!dish) return { error: [404, `Unknown dish: ${body.dish_id}`] }
  const qty = Number(body.qty ?? 1)
  if (!Number.isInteger(qty) || qty < 1 || qty > 20) return { error: [400, 'qty must be an integer between 1 and 20'] }
  const originalText = String(body.text ?? '')
  const clarifications = body.clarifications ?? []
  const text = [originalText, ...clarifications.map((c) => c.answer)].join(', ')
  const t = text.toLowerCase()

  if (/asdf|\?\?\?/.test(t)) return { error: [502, 'Parser could not safely interpret the request.'] }

  const base = {
    order_id: null, dish: { id: dish.id, name: dish.name }, qty, original_text: originalText,
    questions: [], parsed: { ops: [], restrictions: [] },
    as_requested: { feasible: true, reasons: [], kitchen_instructions: [], price_change_cents: 0 },
    options: [], recommended_option_id: null, unavailable_options: [],
    review: { required: false, reasons: [], staff_notes: [], evidence: null },
    customer_message: '', expires_at: null,
  }

  if (/last time|the usual|surprise me|something different/.test(originalText.toLowerCase()) && clarifications.length === 0) {
    return { ...base, status: 'needs_clarification', questions: ["We don't have your previous order. What would you like changed?", 'Any allergies or dietary needs we should know about?'], customer_message: 'Quick question before we send this to the kitchen.' }
  }

  const { ops, restrictions } = parse(dish, text)
  const unknown = ops.find((o) => o.op === 'substitute' && o.with?.startsWith('unknown:'))
  if (unknown && clarifications.length === 0) {
    return { ...base, parsed: { ops, restrictions }, status: 'needs_clarification', questions: [`We don't have "${unknown.with.slice(8)}". What would you like instead of ${nameOf(dish, unknown.ingredient)}?`], customer_message: 'Quick question before we send this to the kitchen.' }
  }

  const evalOut = { ...base, parsed: { ops, restrictions } }
  const reasons = []
  const problems = []
  const removedBase = ops.find((o) => o.op === 'remove' && BASES.includes(o.ingredient))
  if (removedBase) {
    return { ...evalOut, status: 'unavailable', as_requested: { ...base.as_requested, feasible: false, reasons: [`${nameOf(dish, removedBase.ingredient)} is the base of this dish and can't be left out.`] }, customer_message: `Sorry, we can't make ${dish.name} without ${nameOf(dish, removedBase.ingredient)}.` }
  }

  const comps = finalComponents(dish, ops, [])
  for (const id of comps) {
    const bad = conflicts(id, restrictions)
    if (bad.length) { reasons.push(`${nameOf(dish, id)}: contains ${bad.join(', ')}.`); problems.push({ id, name: nameOf(dish, id) }); continue }
    if (!inStock(id, qty)) { reasons.push(`${nameOf(dish, id)} is out of stock.`); problems.push({ id, name: nameOf(dish, id) }) }
  }
  for (const o of ops.filter((o) => o.op === 'substitute')) {
    if (o.with == null || o.with.startsWith('unknown:')) { reasons.push(`Customer wants to swap ${nameOf(dish, o.ingredient)}; pick a replacement.`); if (!problems.some((p) => p.id === o.ingredient)) problems.push({ id: o.ingredient, name: nameOf(dish, o.ingredient) }) }
    else if (!dish.swaps.some((s) => s.replace === o.ingredient && s.with === o.with)) { reasons.push(`${nameOf(dish, o.with)} for ${nameOf(dish, o.ingredient)} is not a chef-approved swap.`); problems.push({ id: o.ingredient, name: nameOf(dish, o.ingredient) }) }
  }

  const feasible = problems.length === 0
  const swapDelta = ops.filter((o) => o.op === 'substitute' && o.with).reduce((s, o) => s + (dish.swaps.find((x) => x.replace === o.ingredient && x.with === o.with)?.price_delta_cents ?? 0), 0)
  evalOut.as_requested = {
    feasible,
    reasons,
    kitchen_instructions: feasible ? instructionsFor(dish, ops, [], restrictions) : [],
    price_change_cents: feasible ? (extrasPrice(dish, ops) + swapDelta) * qty : 0,
  }
  const finalComps = finalComponents(dish, ops, [])
  if (restrictions.length) {
    evalOut.review = {
      required: true,
      reasons: restrictions.map((r) => `${r.kind}: ${r.value}`),
      staff_notes: [`Customer mentioned ${restrictions.map((r) => (r.kind === 'allergy' ? `a ${r.value} allergy` : `a ${r.value} diet`)).join(' and ')}. Confirm every component and cross-contact before approving.`],
      evidence: evidence(dish, finalComps, restrictions),
    }
  }

  if (!feasible) {
    const { options, unavailable } = buildOptions(dish, problems, ops, restrictions, qty)
    evalOut.options = options
    evalOut.unavailable_options = unavailable
    if (!options.length) {
      return { ...evalOut, status: 'unavailable', customer_message: `Sorry, we can't make ${dish.name} that way right now. ${reasons.join(' ')}` }
    }
    evalOut.recommended_option_id = options[0].option_id
    if (restrictions.length) evalOut.review.evidence = evidence(dish, finalComponents(dish, ops, options[0].changes), restrictions)
  }

  if (restrictions.length) {
    const proposed = evalOut.options[0]?.label ?? 'as requested'
    return { ...evalOut, status: 'needs_staff_review', customer_message: `Proposed: ${dish.name} (${proposed}). The kitchen is confirming your ${restrictions.map((r) => r.value).join(', ')} request.` }
  }
  if (!feasible) return { ...evalOut, status: 'needs_customer_approval', customer_message: `We can't make it exactly as asked. ${reasons.join(' ')} Pick one of these instead.` }
  return { ...evalOut, status: 'ready', customer_message: 'We can make this as you asked.' }
}

function createOrder(e, body) {
  const id = `ord-${String(nextOrder++).padStart(4, '0')}`
  const now = Date.now()
  const awaitingStaff = e.status === 'needs_staff_review'
  e.order_id = id
  e.expires_at = iso(now + HOLD_MS)
  const selected = e.recommended_option_id
  const order = {
    order_id: id,
    status: awaitingStaff ? 'awaiting_staff' : 'awaiting_customer',
    created_at: iso(now), expires_at: e.expires_at,
    channel: body.channel ?? null, customer_name: body.customer_name ?? null,
    dish: e.dish, qty: e.qty, original_text: e.original_text,
    selected_option_id: selected,
    kitchen_instructions: selected ? e.options.find((o) => o.option_id === selected).kitchen_instructions : e.as_requested.kitchen_instructions,
    evaluation: clone(e),
    history: [{ at: iso(now), event: 'created', note: e.status }],
  }
  orders.unshift(order)
  return order
}

function selectOption(order, optionId) {
  const e = order.evaluation
  if (optionId == null) {
    if (!e.as_requested.feasible) return [400, 'The request as asked is not feasible; choose an option.']
    order.selected_option_id = null
    order.kitchen_instructions = e.as_requested.kitchen_instructions
    return null
  }
  const opt = e.options.find((o) => o.option_id === optionId)
  if (!opt) return [400, `Unknown option: ${optionId}`]
  order.selected_option_id = optionId
  order.kitchen_instructions = opt.kitchen_instructions
  return null
}

function deduct(order) {
  const e = order.evaluation
  const opt = e.options.find((o) => o.option_id === order.selected_option_id)
  const dish = MENU.find((d) => d.id === order.dish.id)
  for (const id of finalComponents(dish, e.parsed.ops, opt?.changes ?? [])) {
    const c = COMPONENTS[id]
    const s = c && stockItem(c[0])
    if (s) s.on_hand = Math.max(0, s.on_hand - c[1] * order.qty)
  }
}

function acceptOrder(order, optionId) {
  if (order.status !== 'awaiting_customer') return [409, `Order is ${order.status}; it can't be accepted.`]
  const err = selectOption(order, optionId)
  if (err) return err
  const now = iso(Date.now())
  order.history.push({ at: now, event: 'accepted', note: optionId ?? 'as requested' })
  if (order.evaluation.review.required) {
    order.status = 'awaiting_staff'
  } else {
    order.status = 'confirmed'
    deduct(order)
  }
  return null
}

function sweep() {
  const now = Date.now()
  for (const o of orders) {
    if ((o.status === 'awaiting_customer' || o.status === 'awaiting_staff') && Date.parse(o.expires_at) <= now) {
      o.status = 'expired'
      o.history.push({ at: iso(now), event: 'expired', note: 'Hold expired; stock released.' })
    }
  }
}

const ok = (body) => ({ status: 200, body: clone(body) })
const fail = (status, detail) => ({ status, body: { detail } })

export async function handle(method, path, body = {}) {
  await new Promise((r) => setTimeout(r, 150 + Math.random() * 200)) // feel like a network
  sweep()
  let m
  if (method === 'GET' && path === '/api/health') return ok({ status: 'ok' })
  if (method === 'GET' && path === '/api/menu') return ok(MENU)
  if (method === 'POST' && path === '/api/evaluate') {
    const e = evaluate(body)
    if (e.error) return fail(...e.error)
    if (e.status === 'ready' || e.status === 'needs_customer_approval' || e.status === 'needs_staff_review') createOrder(e, body)
    return ok(e)
  }
  if (method === 'GET' && path === '/api/orders') return ok(orders)
  if ((m = path.match(/^\/api\/orders\/([^/]+)(?:\/(accept|review|cancel))?$/))) {
    const order = orders.find((o) => o.order_id === decodeURIComponent(m[1]))
    if (!order) return fail(404, `Unknown order: ${m[1]}`)
    if (method === 'GET' && !m[2]) return ok(order)
    if (method === 'POST' && m[2] === 'cancel') {
      if (!['awaiting_customer', 'awaiting_staff'].includes(order.status)) return fail(409, `Order is ${order.status}; it can't be cancelled.`)
      order.status = 'cancelled'
      order.history.push({ at: iso(Date.now()), event: 'cancelled', note: 'customer cancelled' })
      return ok(order)
    }
    if (method === 'POST' && m[2] === 'accept') {
      const err = acceptOrder(order, body.option_id ?? null)
      return err ? fail(...err) : ok(order)
    }
    if (method === 'POST' && m[2] === 'review') {
      if (order.status !== 'awaiting_staff') return fail(409, `Order is ${order.status}; nothing to review.`)
      const now = iso(Date.now())
      if (body.action === 'reject') {
        order.status = 'rejected'
        order.staff_note = body.note || null
        order.history.push({ at: now, event: 'rejected', note: body.note ?? '' })
        return ok(order)
      }
      if (body.action === 'approve' || body.action === 'edit') {
        if (body.action === 'edit' && body.option_id == null) return fail(400, 'edit needs an option_id')
        const err = selectOption(order, body.option_id ?? null)
        if (err) return fail(...err)
        order.status = 'confirmed'
        order.history.push({ at: now, event: body.action === 'edit' ? 'edited' : 'approved', note: body.option_id ?? 'as requested' })
        deduct(order)
        return ok(order)
      }
      return fail(400, `Unknown action: ${body.action}`)
    }
  }
  if (method === 'GET' && path === '/api/stock') return ok({ items: stock.map(publicStock) })
  if (method === 'POST' && (m = path.match(/^\/api\/stock\/([^/]+)$/))) {
    const s = stockItem(decodeURIComponent(m[1]))
    if (!s) return fail(404, `Unknown stock item: ${m[1]}`)
    const n = Number(body.on_hand)
    if (!Number.isFinite(n) || n < 0) return fail(400, 'on_hand must be a number >= 0')
    s.on_hand = n
    return ok(publicStock(s))
  }
  if (method === 'POST' && path === '/api/demo/reset') {
    resetState(false)
    return ok({ status: 'ok' })
  }
  return fail(404, `No mock for ${method} ${path}`)
}

resetState(true)
