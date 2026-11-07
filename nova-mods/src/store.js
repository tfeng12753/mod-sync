import { INGREDIENTS, INITIAL_STOCK, MENU } from './data/menu.js'
import { effectiveIngredients } from './logic/parser.js'
import { rankSubstitutes } from './logic/substitutions.js'
import { SUB_TIMEOUT_MS, activeMods, blockers, describeMod, lastOffer } from './logic/order.js'

// Which setting controls which kind of substitution
export const SETTING_FOR_REASON = {
  out_of_stock: 'queueOutOfStock',
  allergen: 'queueAllergen',
  requested: 'queueRequested',
}

const SETTING_LABELS = {
  queueOutOfStock: 'Out-of-stock swaps',
  queueAllergen: 'Allergy-conflict swaps',
  queueRequested: 'Customer-requested changes',
}

export const REQUEST_TYPE_LABELS = {
  swap: 'Swaps',
  remove: '"No …"',
  add: '"Extra …"',
  side: '"On the side"',
}

// true  = the kitchen must say "possible" before anything happens
// false = skip the queue (out-of-stock/allergy: straight to the customer; requested: applied as asked)
export function defaultSettings() {
  return {
    queueOutOfStock: true,
    queueAllergen: true,
    queueRequested: false,
    // which kinds of customer request wait for the kitchen (only used when queueRequested is on)
    requestedTypes: { swap: true, remove: true, add: true, side: false },
  }
}

export function initialState() {
  return { orders: [], stock: { ...INITIAL_STOCK }, now: Date.now(), nextId: 1001, log: [], settings: defaultSettings() }
}

function addLog(state, text) {
  return [{ t: Date.now(), text }, ...state.log].slice(0, 30)
}

// The ingredients that are actually "live" in the dish right now, taking customer requests into
// account. Used to decide where out-of-stock / allergy checks are needed.
function liveIngredients(order, subs) {
  const base = MENU[order.itemId].ingredients
  let list = effectiveIngredients(order.itemId, activeMods({ ...order, subs }))
  const skip = new Set()
  for (const s of subs) {
    if (s.reason !== 'requested') continue
    // an ingredient the customer introduced and the kitchen hasn't decided on: don't double-flag it
    if (['staff_review', 'pending'].includes(s.status) && !base.includes(s.target)) skip.add(s.target)
    if (s.modType === 'swap' && ['approved', 'fallback'].includes(s.status) && s.chosenId !== s.target) {
      list = list.map((i) => (i === s.target ? s.chosenId : i))
    }
    if (s.modType === 'remove' && ['approved', 'fallback'].includes(s.status) && s.chosenId !== s.requestedId && !list.includes(s.chosenId)) {
      list = [...list, s.chosenId]
    }
  }
  return list.filter((i) => !skip.has(i))
}

// If a request can't be done, the closest replacements to offer instead
//  - swap:   other ingredients like the original
//  - remove: replacements for the ingredient that can't come out (not something already on the dish)
//  - add/side: nothing sensible to offer
function alternativesFor(sub, order, stock) {
  if (sub.modType === 'swap') return rankSubstitutes(sub.missing, order, stock).options.filter((a) => a.id !== sub.requestedId).slice(0, 3)
  if (sub.modType === 'remove') return rankSubstitutes(sub.missing, order, stock, MENU[order.itemId].ingredients).options.slice(0, 3)
  return []
}

// Hand a proposal to the customer: nothing is applied until they approve it
const toCustomer = (sub, patch = {}) => ({ ...sub, ...patch, status: 'pending', deadline: Date.now() + SUB_TIMEOUT_MS })

// Look at every open order and create proposals where needed:
//  - customer requests (swap / no / extra / side), only for the kinds switched on in the queue rules
//  - an ingredient is out of stock
//  - an ingredient conflicts with a declared allergy (ALWAYS detected, even if its queue is off)
function reconcile(state) {
  let log = state.log
  const say = (text) => { log = [{ t: Date.now(), text }, ...log].slice(0, 30) }

  const orders = state.orders.map((order) => {
    if (order.status === 'handed_off' || order.status === 'removed') return order
    const subs = [...order.subs]
    let changed = false
    const onItem = MENU[order.itemId].ingredients

    // 1. Customer-requested changes
    if (state.settings.queueRequested) {
      for (const m of order.mods) {
        if (!state.settings.requestedTypes[m.type]) continue
        const key = m.type === 'swap' ? m.from : m.ingredient
        if (m.type === 'swap' && (m.from === m.to || !onItem.includes(m.from))) continue
        if (m.type === 'remove' && !onItem.includes(m.ingredient)) continue
        if (subs.some((s) => s.reason === 'requested' && s.modType === m.type && s.missing === key)) continue

        const wantedId = m.type === 'swap' ? m.to : m.ingredient // the ingredient the customer is asking to bring in
        const wanted = INGREDIENTS[wantedId]
        let warning = null
        if (m.type === 'swap' || m.type === 'add') {
          const conflict = wanted.allergens.find((a) => order.allergens.some((x) => x.name === a))
          const out = (state.stock[wantedId] ?? 0) <= 0
          warning = conflict ? `${wanted.name} contains ${conflict} (customer allergy)` : out ? `${wanted.name} is out of stock` : null
        }
        subs.push({
          id: `requested:${m.type}:${key}`,
          reason: 'requested',
          modType: m.type,
          missing: key,
          target: wantedId,
          requestedId: wantedId,
          detail: `Customer asked: ${describeMod(m)}`,
          warning,
          options: [{ id: wantedId, name: wanted.name, score: null, reason: 'Requested by the customer', extraCost: 0 }],
          blocked: [],
          rejected: [],
          chosenId: wantedId,
          status: 'staff_review',
          deadline: null,
        })
        say(`#${order.id}: customer wants ${describeMod(m)}; queued for kitchen decision`)
        changed = true
      }
    }

    // 2. Out of stock / allergy conflicts
    for (const ingId of liveIngredients(order, subs)) {
      if (subs.some((s) => s.reason !== 'requested' && s.missing === ingId)) continue
      const ing = INGREDIENTS[ingId]
      const conflict = ing.allergens.find((a) => order.allergens.some((x) => x.name === a))
      const out = (state.stock[ingId] ?? 0) <= 0
      if (!conflict && !out) continue

      const reason = conflict ? 'allergen' : 'out_of_stock'
      const queued = state.settings[SETTING_FOR_REASON[reason]]
      const { options, blocked } = rankSubstitutes(ingId, order, state.stock)
      const toKitchen = options.length > 0 && queued // otherwise straight to the customer
      const sub = {
        id: `${reason}:${ingId}`,
        missing: ingId,
        reason,
        detail: conflict ? `Contains ${conflict} (customer allergy)` : 'Out of stock',
        options: options.slice(0, 3),
        blocked,
        rejected: [],
        chosenId: options[0]?.id ?? null,
        offer: options.length ? 'replacement' : 'remove_item', // no safe replacement: last resort is taking the item off the order
        status: 'staff_review',
        deadline: null,
      }
      subs.push(toKitchen ? sub : toCustomer(sub))
      say(
        `#${order.id}: ${ing.name} ${conflict ? 'conflicts with allergy' : 'is out'}; ` +
          (!options.length ? 'NO safe replacement, asking customer to approve removing the item' : queued ? `queued ${options[0].name} for kitchen decision` : `sent ${options[0].name} straight to customer`),
      )
      changed = true
    }
    return changed ? { ...order, subs } : order
  })
  return { ...state, orders, log }
}

// Turning a queue rule OFF releases anything of that kind still waiting for the kitchen
function releaseQueued(state, matches) {
  let released = 0
  const orders = state.orders.map((o) => {
    if (!o.subs.some((s) => s.status === 'staff_review' && matches(s))) return o
    return {
      ...o,
      subs: o.subs.map((s) => {
        if (s.status !== 'staff_review' || !matches(s)) return s
        released++
        // a customer request is simply granted (they asked for it); a proposed swap goes to the customer
        if (s.reason === 'requested' && s.options[0]?.id === s.requestedId) return { ...s, status: 'approved', chosenId: s.requestedId }
        return toCustomer(s, { offer: 'replacement', chosenId: s.options[0].id })
      }),
    }
  })
  return { ...state, orders, log: released ? addLog(state, `Queue rule turned off: ${released} waiting item(s) released`) : state.log }
}

function updateOrder(state, orderId, fn) {
  return { ...state, orders: state.orders.map((o) => (o.id === orderId ? fn(o) : o)) }
}

export function reducer(state, action) {
  switch (action.type) {
    case 'ORDER_RECEIVED': {
      const order = { ...action.order, id: state.nextId, createdAt: Date.now() }
      const next = { ...state, orders: [order, ...state.orders], nextId: state.nextId + 1 }
      return reconcile({ ...next, log: addLog(next, `#${order.id} received from ${order.channel}`) })
    }

    case 'INGREDIENT_OUT': {
      const next = { ...state, stock: { ...state.stock, [action.id]: 0 } }
      return reconcile({ ...next, log: addLog(state, `${INGREDIENTS[action.id].name} marked out of stock`) })
    }

    case 'RESTOCK_ALL':
      return { ...state, stock: { ...INITIAL_STOCK }, log: addLog(state, 'Everything restocked') }

    case 'SET_SETTING': {
      const settings = { ...state.settings, [action.key]: action.value }
      let next = { ...state, settings, log: addLog(state, `Kitchen queue for ${SETTING_LABELS[action.key]} turned ${action.value ? 'ON' : 'OFF'}`) }
      if (!action.value) {
        const reason = Object.keys(SETTING_FOR_REASON).find((r) => SETTING_FOR_REASON[r] === action.key)
        next = releaseQueued(next, (s) => s.reason === reason)
      }
      return reconcile(next)
    }

    // Choose which kinds of customer request wait for the kitchen
    case 'SET_REQUEST_TYPE': {
      const settings = { ...state.settings, requestedTypes: { ...state.settings.requestedTypes, [action.modType]: action.value } }
      let next = { ...state, settings, log: addLog(state, `Kitchen queue for ${REQUEST_TYPE_LABELS[action.modType]} requests turned ${action.value ? 'ON' : 'OFF'}`) }
      if (!action.value) next = releaseQueued(next, (s) => s.reason === 'requested' && s.modType === action.modType)
      return reconcile(next)
    }

    // Kitchen says whether the proposed change is actually possible
    case 'STAFF_DECISION': {
      const { orderId, subId, decision } = action
      let log = state.log
      const say = (t) => { log = [{ t: Date.now(), text: t }, ...log].slice(0, 30) }
      const orders = state.orders.map((o) => {
        if (o.id !== orderId) return o
        return {
          ...o,
          subs: o.subs.map((s) => {
            if (s.id !== subId || s.status !== 'staff_review') return s
            const cand = s.options[0]
            const isTheRequest = s.reason === 'requested' && cand.id === s.requestedId
            const asked = describeMod({ type: s.modType, ingredient: s.missing, from: s.missing, to: s.requestedId })

            if (decision === 'possible') {
              if (isTheRequest) {
                say(`#${o.id}: kitchen confirmed ${asked}`)
                return { ...s, status: 'approved', chosenId: cand.id } // the customer asked for it, nothing to approve
              }
              say(`#${o.id}: kitchen OK'd ${cand.name}, sent to customer to approve`)
              return toCustomer(s, { offer: 'replacement', chosenId: cand.id })
            }

            // Not possible: try the next replacement; when none are left the customer is asked to approve the fallback
            const rest = isTheRequest ? alternativesFor(s, o, state.stock) : s.options.slice(1)
            const next = { ...s, rejected: [...(s.rejected || []), cand], options: rest, chosenId: rest[0]?.id ?? null }
            if (rest.length) {
              say(`#${o.id}: kitchen can't do ${isTheRequest ? asked : cand.name}; next: ${rest[0].name}`)
              return { ...next, status: 'staff_review' }
            }
            const offer = lastOffer(s)
            say(`#${o.id}: kitchen can't do ${isTheRequest ? asked : cand.name}; asking customer ${offer === 'as_is' ? 'to continue without it' : 'to approve removing the item'}`)
            return toCustomer(next, { offer })
          }),
        }
      })
      return reconcile({ ...state, orders, log })
    }

    // The customer answers (or staff enter their answer after phoning them)
    //   response: 'approve' (take the replacement) | 'keep' (carry on without the change) | 'remove' (take the item off the order)
    case 'SUB_RESPOND': {
      const { orderId, subId, response, chosenId } = action
      let removedBy = null
      const next = updateOrder(state, orderId, (o) => {
        const subs = o.subs.map((s) => {
          if (s.id !== subId || !['pending', 'no_reply'].includes(s.status)) return s
          if (response === 'approve') return { ...s, status: 'approved', chosenId: chosenId ?? s.chosenId }
          if (response === 'keep' && s.reason === 'requested') return { ...s, status: 'kept_original' }
          removedBy = s
          return { ...s, status: 'item_removed' }
        })
        return { ...o, subs, status: removedBy ? 'removed' : o.status }
      })
      return reconcile(removedBy ? { ...next, log: addLog(next, `#${orderId}: item removed from the order (customer approved), refund issued`) } : next)
    }

    case 'TICK': {
      let log = state.log
      const orders = state.orders.map((o) => {
        if (!o.subs.some((s) => s.status === 'pending' && s.deadline <= action.now)) return o
        return {
          ...o,
          subs: o.subs.map((s) => {
            if (s.status === 'pending' && s.deadline <= action.now) {
              // nothing is ever applied without the customer's OK: staff have to call them
              log = [{ t: action.now, text: `#${o.id}: customer hasn't replied, staff need to call them` }, ...log].slice(0, 30)
              return { ...s, status: 'no_reply' }
            }
            return s
          }),
        }
      })
      return reconcile({ ...state, now: action.now, orders, log })
    }

    case 'TOGGLE_PACK':
      return updateOrder(state, action.orderId, (o) => ({ ...o, packed: { ...o.packed, [action.key]: !o.packed[action.key] } }))

    case 'RESOLVE_REVIEW':
      return updateOrder(state, action.orderId, (o) => ({ ...o, needsReview: false, reviewReasons: [] }))

    case 'HAND_OFF':
      return updateOrder(state, action.orderId, (o) => (o.status === 'removed' || blockers(o).length ? o : { ...o, status: 'handed_off' }))

    case 'RESET':
      return { ...initialState(), settings: state.settings } // keep the queue rules when resetting the demo

    default:
      return state
  }
}
