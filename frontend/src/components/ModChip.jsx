import { describeMod } from '../logic/order.js'

export default function ModChip({ mod }) {
  return <span className={`chip chip-${mod.type}`}>{describeMod(mod)}</span>
}
