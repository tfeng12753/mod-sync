import { INGREDIENTS, MENU } from '../data/menu.js'
import { parseModifiers, effectiveIngredients } from './parser.js'

export const SUB_TIMEOUT_MS = 12000 // how long we wait for the customer before the fallback applies

export function ingName(id) {
  return INGREDIENTS[id]?.name ?? id
}

export function describeMod(m) {
  if (m.type === 'remove') return `NO ${ingName(m.ingredient)}`
  if (m.type === 'add') return `EXTRA ${ingName(m.ingredient)}`
  if (m.type === 'side') return `${ingName(m.ingredient)} ON THE SIDE`
  if (m.type === 'swap') return `${ingName(m.from)} → ${ingName(m.to)}`
  return m.type
}

// Build a full order object from raw user input (this is where the parser is called)
export function makeOrder({ channel, name, itemId, rawNote, declaredAllergens = [], customerPrefers = [], restrictions = [] }) {
  const parsed = parseModifiers(itemId, rawNote, declaredAllergens)
  return {
    channel,
    name,
    itemId,
    rawNote,
    mods: parsed.mods,
    allergens: parsed.allergens,
    needsReview: parsed.needsReview,
    reviewReasons: parsed.reviewReasons,
    customerPrefers,
    restrictions,
    packed: {},
    subs: [],
    status: 'new', // new -> ready -> handed_off
  }
}

// ---- Customer-requested changes that go through the kitchen queue ------------------------
// A "requested" sub covers one customer modification: swap / remove ("no x") / add ("extra x") / side.

// Rebuild the modifier a requested sub stands for
export function subToMod(s) {
  return s.modType === 'swap' ? { type: 'swap', from: s.missing, to: s.requestedId } : { type: s.modType, ingredient: s.missing }
}

// The queue item (if any) that governs this modifier
export function requestedSubFor(order, m) {
  const key = m.type === 'swap' ? m.from : m.ingredient
  return order.subs.find((s) => s.reason === 'requested' && s.modType === m.type && s.missing === key)
}

// Modifiers still in force: a request the kitchen said "not possible" to is dropped
export function activeMods(order) {
  return order.mods.filter((m) => requestedSubFor(order, m)?.status !== 'kept_original')
}

// When every replacement has been used up, what does the customer get asked?
//  'as_is'       = continue without the change (swap/extra/side requests: dish stays as on the menu)
//  'remove_item' = last resort, the whole item comes off the order (refund)
export function lastOffer(sub) {
  return sub.reason === 'requested' && sub.modType !== 'remove' ? 'as_is' : 'remove_item'
}

// The reply that means "yes, that's fine" for whatever the customer is being asked
export function acceptResponse(sub) {
  return sub.offer === 'replacement' ? 'approve' : sub.offer === 'as_is' ? 'keep' : 'remove'
}

// Short text for "request was refused, dish stays as on the menu"
export function keptText(s) {
  const n = ingName(s.missing)
  if (s.modType === 'remove') return `Keep ${n} (can't be left out)`
  if (s.modType === 'add') return `No extra ${n} (not possible)`
  if (s.modType === 'side') return `${n} stays on the food (can't go on the side)`
  return `Keep original ${n} (swap not possible)`
}

// Ingredients actually in the food, after mods AND any accepted substitutions
export function finalIngredients(order) {
  let list = effectiveIngredients(order.itemId, activeMods(order))

  for (const s of order.subs.filter((x) => x.reason === 'requested')) {
    if (!['approved', 'fallback'].includes(s.status)) continue
    // the kitchen offered a different swap than the one requested
    if (s.modType === 'swap' && s.chosenId !== s.target) list = list.map((i) => (i === s.target ? s.chosenId : i))
    // "no onion" couldn't be done as asked: the customer accepted a replacement instead
    if (s.modType === 'remove' && s.chosenId !== s.requestedId && !list.includes(s.chosenId)) list = [...list, s.chosenId]
  }
  // Out-of-stock / allergy swaps
  for (const s of order.subs.filter((x) => x.reason !== 'requested')) {
    if ((s.status === 'approved' || s.status === 'fallback') && s.chosenId) {
      list = list.map((i) => (i === s.missing ? s.chosenId : i))
    }
  }
  return list
}

// The pack-check list the staff member has to tick before handoff
export function packChecklist(order) {
  const items = []
  // A change that goes through the queue is listed once (as a request), not twice
  order.mods.forEach((m, i) => {
    if (requestedSubFor(order, m)) return
    items.push({ key: `mod-${i}`, label: describeMod(m), kind: 'mod' })
  })
  order.allergens.forEach((a) => items.push({ key: `allergen-${a.name}`, label: `ALLERGY: ${a.name.toUpperCase()} - check all components`, kind: 'allergen' }))
  order.subs
    .filter((s) => ['approved', 'fallback'].includes(s.status))
    .forEach((s) => {
      const label =
        s.reason !== 'requested'
          ? `Substitution: ${ingName(s.missing)} → ${ingName(s.chosenId)}`
          : s.modType === 'swap'
            ? `Customer swap: ${ingName(s.missing)} → ${ingName(s.chosenId)}`
            : s.modType === 'remove' && s.chosenId !== s.requestedId
              ? `Customer request: NO ${ingName(s.missing)}, replaced with ${ingName(s.chosenId)}`
              : `Customer request: ${describeMod(subToMod(s))}`
      items.push({ key: `${s.reason === 'requested' ? 'req' : 'sub'}-${s.id ?? s.missing}`, label, kind: 'sub' })
    })
  order.subs
    .filter((s) => s.status === 'kept_original')
    .forEach((s) => items.push({ key: `keep-${s.id ?? s.missing}`, label: keptText(s), kind: 'sub' }))
  items.push({ key: 'bag', label: 'Bag sealed and labelled with order # and name', kind: 'general' })
  return items
}

// Why can't this order be handed off yet?
export function blockers(order) {
  const b = []
  if (order.needsReview) b.push('Parser needs a human to confirm the instructions')
  if (order.subs.some((s) => s.status === 'staff_review')) b.push('A change is waiting for a kitchen decision')
  if (order.subs.some((s) => s.status === 'pending')) b.push('Waiting on customer to approve a change')
  if (order.subs.some((s) => s.status === 'no_reply')) b.push("Customer hasn't replied: call them")
  const open = packChecklist(order).filter((c) => !order.packed[c.key])
  if (open.length) b.push(`${open.length} pack check${open.length > 1 ? 's' : ''} still open`)
  return b
}

export function itemLabel(order) {
  return MENU[order.itemId].name
}
