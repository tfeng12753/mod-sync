import { INGREDIENTS, INITIAL_STOCK } from './data/menu.js'
import { effectiveIngredients } from './logic/parser.js'
import { rankSubstitutes } from './logic/substitutions.js'
import { SUB_TIMEOUT_MS, blockers } from './logic/order.js'

export function initialState() {
  return { orders: [], stock: { ...INITIAL_STOCK }, now: Date.now(), nextId: 1001, log: [] }
}

function addLog(state, text) {
  return [{ t: Date.now(), text }, ...state.log].slice(0, 30)
}

// Look at every open order and create substitution proposals where needed:
//  - an ingredient is out of stock, OR
//  - an ingredient conflicts with a declared allergy
function reconcile(state) {
  let log = state.log
  const orders = state.orders.map((order) => {
    if (order.status === 'handed_off') return order
    const subs = [...order.subs]
    let changed = false

    for (const ingId of effectiveIngredients(order.itemId, order.mods)) {
      if (subs.some((s) => s.missing === ingId)) continue
      const ing = INGREDIENTS[ingId]
      const conflict = ing.allergens.find((a) => order.allergens.some((x) => x.name === a))
      const out = (state.stock[ingId] ?? 0) <= 0
      if (!conflict && !out) continue

      const { options, blocked } = rankSubstitutes(ingId, order, state.stock)
      subs.push({
        missing: ingId,
        reason: conflict ? 'allergen' : 'out_of_stock',
        detail: conflict ? `Contains ${conflict} (customer allergy)` : 'Out of stock',
        options: options.slice(0, 3),
        blocked,
        chosenId: options[0]?.id ?? null,
        status: options.length ? 'pending' : 'no_safe_option',
        deadline: Date.now() + SUB_TIMEOUT_MS,
      })
      log = [{ t: Date.now(), text: `#${order.id}: ${ing.name} ${conflict ? 'conflicts with allergy' : 'is out'}; ${options.length ? `proposed ${options[0].name}` : 'NO safe substitute'}` }, ...log].slice(0, 30)
      changed = true
    }
    return changed ? { ...order, subs } : order
  })
  return { ...state, orders, log }
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

    case 'SUB_RESPOND': {
      const { orderId, missing, response, chosenId } = action
      return updateOrder(state, orderId, (o) => ({
        ...o,
        subs: o.subs.map((s) => {
          if (s.missing !== missing || s.status !== 'pending') return s
          if (response === 'approve') return { ...s, status: 'approved', chosenId: chosenId ?? s.chosenId }
          return { ...s, status: 'declined' }
        }),
      }))
    }

    case 'TICK': {
      let log = state.log
      const orders = state.orders.map((o) => {
        if (!o.subs.some((s) => s.status === 'pending' && s.deadline <= action.now)) return o
        return {
          ...o,
          subs: o.subs.map((s) => {
            if (s.status === 'pending' && s.deadline <= action.now) {
              log = [{ t: action.now, text: `#${o.id}: no reply, fallback applied (${INGREDIENTS[s.chosenId].name})` }, ...log].slice(0, 30)
              return { ...s, status: 'fallback' }
            }
            return s
          }),
        }
      })
      return { ...state, now: action.now, orders, log }
    }

    case 'TOGGLE_PACK':
      return updateOrder(state, action.orderId, (o) => ({ ...o, packed: { ...o.packed, [action.key]: !o.packed[action.key] } }))

    case 'RESOLVE_REVIEW':
      return updateOrder(state, action.orderId, (o) => ({ ...o, needsReview: false, reviewReasons: [] }))

    case 'HAND_OFF':
      return updateOrder(state, action.orderId, (o) => (blockers(o).length ? o : { ...o, status: 'handed_off' }))

    case 'RESET':
      return initialState()

    default:
      return state
  }
}
