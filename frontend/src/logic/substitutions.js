import { INGREDIENTS } from '../data/menu.js'

// Weights are intentionally visible: they make a good slide ("tunable by the owner").
export const WEIGHTS = { similarity: 0.4, preference: 0.25, margin: 0.2, prep: 0.15 }

function jaccard(a, b) {
  const A = new Set(a), B = new Set(b)
  const inter = [...A].filter((x) => B.has(x)).length
  const union = new Set([...A, ...B]).size
  return union === 0 ? 0 : inter / union
}

/**
 * Rank safe replacements for a missing ingredient.
 * Returns { options: [...ranked safe candidates], blocked: [{id, name, reason}] }
 */
export function rankSubstitutes(missingId, order, stock) {
  const missing = INGREDIENTS[missingId]
  const allergens = (order.allergens || []).map((a) => a.name)
  const prefers = order.customerPrefers || []
  const vegetarian = (order.restrictions || []).includes('vegetarian')

  const options = []
  const blocked = []

  const candidates = Object.values(INGREDIENTS).filter(
    (c) => c.id !== missingId && c.category === missing.category,
  )

  for (const c of candidates) {
    if ((stock[c.id] ?? 0) <= 0) continue // out of stock: just skip

    const hit = c.allergens.find((a) => allergens.includes(a))
    if (hit) {
      blocked.push({ id: c.id, name: c.name, reason: `Contains ${hit} (customer allergy)` })
      continue
    }
    if (vegetarian && (c.diet || []).includes('meat')) {
      blocked.push({ id: c.id, name: c.name, reason: 'Not vegetarian' })
      continue
    }

    const similarity = jaccard(missing.taste, c.taste)
    const preference = prefers.includes(c.id) ? 1 : 0
    const margin = c.cost <= missing.cost ? 1 : Math.max(0, 1 - (c.cost - missing.cost) / missing.cost)
    const prep = c.prep <= missing.prep ? 1 : Math.max(0, 1 - (c.prep - missing.prep) / Math.max(missing.prep, 1))

    const score =
      WEIGHTS.similarity * similarity + WEIGHTS.preference * preference + WEIGHTS.margin * margin + WEIGHTS.prep * prep

    const parts = []
    if (similarity >= 0.4) parts.push(`similar taste (${Math.round(similarity * 100)}% match)`)
    if (preference) parts.push('customer has ordered it before')
    if (margin === 1) parts.push('no extra cost')
    if (c.allergens.length === 0) parts.push('no allergens')
    else if (!allergens.length) parts.push('no allergy conflict')

    options.push({
      id: c.id,
      name: c.name,
      score: Math.round(score * 100) / 100,
      reason: parts.length ? parts.join(', ') : 'closest available alternative',
      extraCost: Math.max(0, Math.round((c.cost - missing.cost) * 100) / 100),
    })
  }

  options.sort((a, b) => b.score - a.score)
  return { options, blocked }
}
