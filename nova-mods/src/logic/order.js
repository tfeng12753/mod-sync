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

// Ingredients actually in the food, after mods AND any accepted substitutions
export function finalIngredients(order) {
  let list = effectiveIngredients(order.itemId, order.mods)
  for (const s of order.subs) {
    if ((s.status === 'approved' || s.status === 'fallback') && s.chosenId) {
      list = list.map((i) => (i === s.missing ? s.chosenId : i))
    }
    if (s.status === 'declined' || s.status === 'no_safe_option') {
      list = list.filter((i) => i !== s.missing)
    }
  }
  return list
}

// The pack-check list the staff member has to tick before handoff
export function packChecklist(order) {
  const items = []
  order.mods.forEach((m, i) => items.push({ key: `mod-${i}`, label: describeMod(m), kind: 'mod' }))
  order.allergens.forEach((a) => items.push({ key: `allergen-${a.name}`, label: `ALLERGY: ${a.name.toUpperCase()} - check all components`, kind: 'allergen' }))
  order.subs
    .filter((s) => ['approved', 'fallback'].includes(s.status))
    .forEach((s) => items.push({ key: `sub-${s.missing}`, label: `Substitution: ${ingName(s.missing)} → ${ingName(s.chosenId)}`, kind: 'sub' }))
  order.subs
    .filter((s) => ['declined', 'no_safe_option'].includes(s.status))
    .forEach((s) => items.push({ key: `omit-${s.missing}`, label: `Leave out ${ingName(s.missing)} (refund/credit)`, kind: 'sub' }))
  items.push({ key: 'bag', label: 'Bag sealed and labelled with order # and name', kind: 'general' })
  return items
}

// Why can't this order be handed off yet?
export function blockers(order) {
  const b = []
  if (order.needsReview) b.push('Parser needs a human to confirm the instructions')
  if (order.subs.some((s) => s.status === 'pending')) b.push('Waiting on customer to approve a substitution')
  const open = packChecklist(order).filter((c) => !order.packed[c.key])
  if (open.length) b.push(`${open.length} pack check${open.length > 1 ? 's' : ''} still open`)
  return b
}

export function itemLabel(order) {
  return MENU[order.itemId].name
}
