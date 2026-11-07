import { describeMod } from '../logic/order.js'

export default function ModChip({ mod, struck = false }) {
  return <span className={`chip chip-${mod.type} ${struck ? 'chip-struck' : ''}`}>{describeMod(mod)}</span>
}
