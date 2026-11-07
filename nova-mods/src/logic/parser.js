import { INGREDIENTS, ALLERGEN_WORDS, MENU } from '../data/menu.js'

// ---------------------------------------------------------------------------
// MOCK modifier parser (rule-based) so the UI works with no backend.
// Replace `parseModifiers` internals with a call to your LLM endpoint when ready;
// keep the OUTPUT SHAPE the same and the UI won't need to change:
//
// {
//   mods: [{ type: 'remove'|'add'|'side'|'swap', ingredient?, from?, to?, confidence }],
//   allergens: [{ name, severity: 'high' }],
//   needsReview: boolean,
//   reviewReasons: string[]
// }
// ---------------------------------------------------------------------------

const VAGUE = /(like last time|the usual|as usual|surprise me|whatever|you know|same as before)/

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Longest alias first so "sesame bun" beats "bun"
const ALIAS_LIST = Object.values(INGREDIENTS)
  .flatMap((ing) => ing.aliases.map((a) => ({ id: ing.id, alias: a })))
  .sort((a, b) => b.alias.length - a.alias.length)

function findIngredients(text) {
  const found = []
  let rest = text
  for (const { id, alias } of ALIAS_LIST) {
    const re = new RegExp(`\\b${escapeRe(alias)}\\b`)
    if (re.test(rest)) {
      if (!found.includes(id)) found.push(id)
      rest = rest.replace(re, ' ')
    }
  }
  return found
}

function findAllergen(text) {
  for (const [word, canon] of Object.entries(ALLERGEN_WORDS)) {
    if (new RegExp(`\\b${word}\\b`).test(text)) return canon
  }
  return null
}

export function parseModifiers(itemId, rawText = '', declaredAllergens = []) {
  const item = MENU[itemId]
  const text = (rawText || '').toLowerCase().replace(/\s+/g, ' ').trim()
  const result = { mods: [], allergens: [], needsReview: false, reviewReasons: [] }

  // Allergens picked from the dedicated field (always high severity)
  for (const a of declaredAllergens) result.allergens.push({ name: a, severity: 'high' })

  if (!text) return result

  if (VAGUE.test(text)) {
    result.needsReview = true
    result.reviewReasons.push('Vague instruction ("like last time" etc.) - confirm with customer')
  }

  const clauses = text.split(/[,;.\n]| and (?=no |without |extra |sub |swap |add )/).map((c) => c.trim()).filter(Boolean)
  const onItem = new Set(item.ingredients)

  for (const clause of clauses) {
    if (VAGUE.test(clause)) continue

    // Allergy statements
    const allergyMatch = clause.match(/allerg(?:ic|y)(?: to)?\s+(.+)/) || clause.match(/(.+?)\s+allergy/)
    if (allergyMatch) {
      const canon = findAllergen(allergyMatch[1])
      if (canon) {
        if (!result.allergens.some((a) => a.name === canon)) result.allergens.push({ name: canon, severity: 'high' })
      } else {
        result.needsReview = true
        result.reviewReasons.push(`Unrecognised allergy: "${allergyMatch[1]}"`)
      }
      continue
    }

    // Swaps: "sub fries -> salad", "swap fries for salad", "fries to salad"
    const swapMatch = clause.match(/^(?:sub|swap|substitute|replace)?\s*(.+?)\s*(?:->|→|\bfor\b|\bwith\b|\bto\b|\binstead of\b)\s*(.+)$/)
    if (swapMatch && /(sub|swap|replace|->|→|instead)/.test(clause)) {
      const a = findIngredients(swapMatch[1])[0]
      const b = findIngredients(swapMatch[2])[0]
      if (a && b) {
        // "sub salad for fries" is ambiguous: whichever one is on the item is the 'from'
        let from = a, to = b
        if (!onItem.has(a) && onItem.has(b)) { from = b; to = a }
        if (clause.includes('instead of')) { from = b; to = a }
        result.mods.push({ type: 'swap', from, to, confidence: onItem.has(from) ? 0.95 : 0.6 })
        if (!onItem.has(from)) {
          result.needsReview = true
          result.reviewReasons.push(`"${INGREDIENTS[from].name}" is not on this item`)
        }
        continue
      }
    }

    // Removals: "no onion", "without pickles", "hold the tomato"
    const rm = clause.match(/^(?:no|without|hold the|hold|remove|skip)\s+(.+)/)
    if (rm) {
      const ids = findIngredients(rm[1])
      if (ids.length === 0) {
        const canon = findAllergen(rm[1])
        if (canon) {
          if (!result.allergens.some((x) => x.name === canon)) result.allergens.push({ name: canon, severity: 'high' })
        } else {
          result.needsReview = true
          result.reviewReasons.push(`Could not understand: "${clause}"`)
        }
      }
      for (const id of ids) {
        if (onItem.has(id)) result.mods.push({ type: 'remove', ingredient: id, confidence: 0.97 })
        // removing something that is not on the item is harmless: ignore
      }
      continue
    }

    // Extras: "extra pickles", "add avocado"
    const ad = clause.match(/^(?:extra|add|double)\s+(.+)/)
    if (ad) {
      const ids = findIngredients(ad[1])
      if (ids.length === 0) {
        result.needsReview = true
        result.reviewReasons.push(`Could not understand: "${clause}"`)
      }
      for (const id of ids) result.mods.push({ type: 'add', ingredient: id, confidence: 0.93 })
      continue
    }

    // On the side: "sauce on the side", "side of sauce"
    const sd = clause.match(/(.+?)\s+on the side/) || clause.match(/side of\s+(.+)/)
    if (sd) {
      const ids = findIngredients(sd[1])
      if (ids.length === 0) {
        result.needsReview = true
        result.reviewReasons.push(`Could not understand: "${clause}"`)
      }
      for (const id of ids) result.mods.push({ type: 'side', ingredient: id, confidence: 0.92 })
      continue
    }

    // Anything else: don't guess.
    if (clause.length > 2 && !/^(pls|please|thanks|thank you|ty)$/.test(clause)) {
      result.needsReview = true
      result.reviewReasons.push(`Could not understand: "${clause}"`)
    }
  }

  // Low-confidence mods also trigger review
  if (result.mods.some((m) => m.confidence < 0.8)) result.needsReview = true

  return result
}

// Which ingredients will actually be in the food after the mods are applied?
export function effectiveIngredients(itemId, mods = []) {
  let list = [...MENU[itemId].ingredients]
  for (const m of mods) {
    if (m.type === 'remove') list = list.filter((i) => i !== m.ingredient)
    if (m.type === 'swap') list = list.map((i) => (i === m.from ? m.to : i))
    if (m.type === 'add' && !list.includes(m.ingredient)) list.push(m.ingredient)
  }
  return list
}
