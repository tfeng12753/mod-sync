// Pure display helpers for API data (no business logic: the backend decides everything).

export function money(cents) {
  if (cents == null) return '—'
  return `$${(cents / 100).toFixed(2)}`
}

export function priceChange(cents) {
  if (cents == null) return 'price set by staff'
  if (cents === 0) return 'no price change'
  return `${cents > 0 ? '+' : '−'}${money(Math.abs(cents))}`
}

export function prettyId(id) {
  if (!id) return ''
  const s = String(id).replace(/^unknown:/, '').replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// Look up a human name for a component/extra id in a menu dish.
export function nameIn(dish, id) {
  if (!dish || !id) return prettyId(id)
  return (
    dish.components?.find((c) => c.id === id)?.name ??
    dish.extras?.find((c) => c.id === id)?.name ??
    dish.swaps?.find((s) => s.with === id)?.with_name ??
    dish.swaps?.find((s) => s.replace === id)?.replace_name ??
    prettyId(id)
  )
}

// Parsed op -> chip {type, text}. Types map to Jacob's chip colours.
export function describeOp(op, dish) {
  const n = nameIn(dish, op.ingredient)
  // Words we couldn't match to the menu are shown as such, never as a real change.
  if (String(op.ingredient ?? '').startsWith('unknown:')) return { type: 'remove', text: `${n}: not on our menu` }
  switch (op.op) {
    case 'remove': return { type: 'remove', text: `NO ${n}` }
    case 'add': return { type: 'add', text: `EXTRA ${n}${op.qty > 1 ? ` ×${op.qty}` : ''}` }
    case 'on_side': return { type: 'side', text: `${n} ON THE SIDE` }
    case 'less': return { type: 'side', text: `LIGHT ${n}` }
    case 'substitute':
      return { type: 'swap', text: op.with ? `${n} → ${nameIn(dish, op.with)}` : `SWAP ${n} (suggest)` }
    default: return { type: 'swap', text: `${op.op} ${n}` }
  }
}

// Option change -> chip
export function describeChange(c) {
  return c.with ? { type: 'swap', text: `${c.replace_name} → ${c.with_name}` } : { type: 'remove', text: `NO ${c.replace_name}` }
}

export function restrictionText(r) {
  return `${r.kind === 'allergy' ? 'Allergy' : 'Diet'}: ${r.value}`
}

// Questions may come as plain strings or {question, ...} objects.
export function questionText(q) {
  return typeof q === 'string' ? q : q?.question ?? q?.text ?? JSON.stringify(q)
}

export function secondsUntil(isoTime, now) {
  if (!isoTime) return null
  return Math.max(0, Math.round((Date.parse(isoTime) - now) / 1000))
}

export function countdown(isoTime, now) {
  const s = secondsUntil(isoTime, now)
  if (s == null) return ''
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function channelClass(channel) {
  return `ch-${String(channel || 'unknown').toLowerCase().replace(/[^a-z]/g, '')}`
}

export const ORDER_STATUS = {
  awaiting_customer: ['Waiting on customer', 'warn'],
  awaiting_staff: ['Kitchen is confirming', 'warn'],
  confirmed: ['Confirmed', 'ok'],
  rejected: ['Rejected by kitchen', 'bad'],
  expired: ['Expired (no reply)', 'bad'],
  cancelled: ['Cancelled', 'bad'],
}

// Keep the kitchen display focused on recipe changes; restrictions have their own banner.
function isOrderChange(line) {
  return /^(NO|EXTRA|SUB|LIGHT)\b|\bON THE SIDE$/i.test(line)
}

export function orderChanges(lines = []) {
  return lines.filter(isOrderChange)
}

// Only actual order changes require a kitchen check, not routine preparation or packing.
export function packChecklist(order) {
  return (order.kitchen_instructions ?? []).map((line, i) => ({
    key: `ki-${i}`,
    label: line,
    kind: 'mod',
  })).filter((item) => isOrderChange(item.label))
}
