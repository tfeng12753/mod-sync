// chip = {type: 'remove' | 'add' | 'swap' | 'side', text}
export default function ModChip({ chip }) {
  return <span className={`chip chip-${chip.type}`}>{chip.text}</span>
}
