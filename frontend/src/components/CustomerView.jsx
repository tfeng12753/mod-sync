import { useMemo, useState } from 'react'
import { useStore } from '../StoreContext.jsx'
import { MENU } from '../data/menu.js'
import { parseModifiers } from '../logic/parser.js'
import { makeOrder, blockers, itemLabel } from '../logic/order.js'
import ModChip from './ModChip.jsx'
import SubstitutionCard from './SubstitutionCard.jsx'

const ALLERGENS = ['sesame', 'egg', 'dairy', 'gluten', 'soy', 'peanut']

function statusOf(order) {
  if (order.status === 'handed_off') return ['On its way 🛵', 'ok']
  if (order.subs.some((s) => s.status === 'pending')) return ['Needs your OK', 'warn']
  if (order.needsReview) return ['Restaurant is double-checking your note', 'warn']
  if (blockers(order).length === 0) return ['Ready for pickup', 'ok']
  return ['Being prepared', 'info']
}

export default function CustomerView() {
  const { state, dispatch } = useStore()
  const [itemId, setItemId] = useState('classic_burger')
  const [note, setNote] = useState('')
  const [allergens, setAllergens] = useState([])
  const [vegetarian, setVegetarian] = useState(false)

  const preview = useMemo(() => parseModifiers(itemId, note, allergens), [itemId, note, allergens])
  const mine = state.orders.filter((o) => o.name === 'You')

  const toggleAllergen = (a) => setAllergens((cur) => (cur.includes(a) ? cur.filter((x) => x !== a) : [...cur, a]))

  const place = () => {
    dispatch({
      type: 'ORDER_RECEIVED',
      order: makeOrder({ channel: 'DoorDash', name: 'You', itemId, rawNote: note, declaredAllergens: allergens, restrictions: vegetarian ? ['vegetarian'] : [] }),
    })
    setNote('')
  }

  return (
    <div className="phone">
      <h2>Order</h2>

      <div className="menu">
        {Object.values(MENU).map((m) => (
          <button key={m.id} className={`menu-item ${itemId === m.id ? 'selected' : ''}`} onClick={() => setItemId(m.id)}>
            <b>{m.name}</b>
            <span>${m.price.toFixed(2)}</span>
          </button>
        ))}
      </div>

      <label className="field">
        <span>Special instructions</span>
        <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. no onion, sauce on the side, sub fries for salad" />
      </label>

      <div className="field">
        <span>Allergies</span>
        <div className="row wrap">
          {ALLERGENS.map((a) => (
            <button key={a} className={`tog ${allergens.includes(a) ? 'on' : ''}`} onClick={() => toggleAllergen(a)}>{a}</button>
          ))}
          <button className={`tog ${vegetarian ? 'on' : ''}`} onClick={() => setVegetarian(!vegetarian)}>vegetarian</button>
        </div>
      </div>

      <div className="preview">
        <b>Here's how we understood your order</b>
        {preview.mods.length === 0 && preview.allergens.length === 0 && <p className="muted">No changes: made as listed.</p>}
        <div className="chips">{preview.mods.map((m, i) => <ModChip key={i} mod={m} />)}</div>
        {preview.allergens.length > 0 && <div className="allergy-banner">⚠ Allergy: {preview.allergens.map((a) => a.name).join(', ')}</div>}
        {preview.needsReview && <div className="review"><b>We'll confirm this with the restaurant</b><ul>{preview.reviewReasons.map((r, i) => <li key={i}>{r}</li>)}</ul></div>}
      </div>

      <button className="btn primary block" onClick={place}>Place order</button>

      {mine.length > 0 && <h3>Your orders</h3>}
      {mine.map((o) => {
        const [label, tone] = statusOf(o)
        return (
          <div key={o.id} className="my-order">
            <div className="row between"><b>#{o.id} · {itemLabel(o)}</b><span className={`status ${tone}`}>{label}</span></div>
            {o.subs.map((s) => <SubstitutionCard key={s.missing} sub={s} order={o} now={state.now} audience="customer" dispatch={dispatch} />)}
          </div>
        )
      })}
    </div>
  )
}
