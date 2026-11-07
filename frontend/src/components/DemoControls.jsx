import { SAMPLES } from '../data/sampleOrders.js'
import { makeOrder } from '../logic/order.js'

const OUTS = [
  ['house_sauce', 'House sauce'],
  ['sesame_bun', 'Sesame bun'],
  ['avocado', 'Avocado'],
  ['fries', 'Fries'],
]

export default function DemoControls({ state, dispatch }) {
  return (
    <aside className="panel demo">
      <h4>Demo controls</h4>
      <p className="muted">Trigger each scenario on cue.</p>

      <h5>Send an order</h5>
      {Object.entries(SAMPLES).map(([k, s]) => (
        <button key={k} className="btn block" onClick={() => dispatch({ type: 'ORDER_RECEIVED', order: makeOrder(s) })}>
          {s.label}
        </button>
      ))}

      <h5>Run out of…</h5>
      <div className="row wrap">
        {OUTS.map(([id, label]) => (
          <button key={id} className="btn small" disabled={state.stock[id] <= 0} onClick={() => dispatch({ type: 'INGREDIENT_OUT', id })}>
            {state.stock[id] <= 0 ? `${label} (out)` : label}
          </button>
        ))}
      </div>

      <div className="row">
        <button className="btn small ghost" onClick={() => dispatch({ type: 'RESTOCK_ALL' })}>Restock</button>
        <button className="btn small ghost" onClick={() => dispatch({ type: 'RESET' })}>Reset demo</button>
      </div>

      <h5>Activity</h5>
      <ul className="log">
        {state.log.length === 0 && <li className="muted">Nothing yet</li>}
        {state.log.map((l, i) => <li key={i}>{l.text}</li>)}
      </ul>
    </aside>
  )
}
