import { useState } from 'react'
import { useStore } from '../useStore.js'
import { api } from '../api.js'
import { SAMPLES } from '../data/sampleOrders.js'

export default function DemoControls() {
  const { state, dispatch, refreshOrders, refreshStock } = useStore()
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('')
  const [confirmReset, setConfirmReset] = useState(false)
  const [resetMessage, setResetMessage] = useState('')

  const run = async (key, fn) => {
    setBusy(key)
    setError(null)
    setResetMessage('')
    try {
      await fn()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  const send = (s) => run(`send-${s.label}`, async () => {
    const e = await api.evaluate(s)
    if (e.order_id) dispatch({ type: 'MINE', orderId: e.order_id })
    dispatch({ type: 'LOG', text: `${e.order_id ?? 'no order'}: ${s.label} → ${e.status}` })
    await refreshOrders()
  })

  const markOut = (item) => run(`out-${item.id}`, async () => {
    const updated = await api.setStock(item.id, 0, 'ran out (demo)')
    dispatch({ type: 'STOCK_ITEM', item: updated })
    dispatch({ type: 'LOG', text: `${item.name} marked out of stock` })
  })

  const restock = (item) => run(`restock-${item.id}`, async () => {
    const updated = await api.setStock(item.id, item.initial_on_hand, 'restocked (demo)')
    dispatch({ type: 'STOCK_ITEM', item: updated })
    dispatch({ type: 'LOG', text: `${item.name} restocked` })
  })

  const reset = () => run('reset', async () => {
    await api.resetDemo()
    dispatch({ type: 'RESET_CLIENT' })
    await Promise.all([refreshOrders(), refreshStock()])
    setConfirmReset(false)
    setResetMessage('Backend cleared. Orders and holds removed; starting stock restored.')
  })

  const f = filter.trim().toLowerCase()
  const items = state.stock.filter((s) => !f || s.name.toLowerCase().includes(f) || s.id.includes(f))

  return (
    <aside className="panel demo">
      <h4>Demo controls</h4>
      <p className="muted">Trigger each scenario on cue. All data is synthetic.</p>
      <p className="muted">Open Customer app → Your orders to review and accept demo orders.</p>
      {error && <div className="error">{error}</div>}
      {resetMessage && <p role="status">{resetMessage}</p>}

      <h5>Clear backend</h5>
      <p className="muted">Remove all demo orders and holds, and restore starting stock for everyone.</p>
      {confirmReset ? (
        <div role="group" aria-label="Confirm backend reset">
          <p>Clear the shared demo backend now?</p>
          <div className="row">
            <button className="btn small" disabled={!!busy} onClick={reset}>
              {busy === 'reset' ? 'Clearing…' : 'Clear now'}
            </button>
            <button className="btn small ghost" disabled={!!busy} onClick={() => setConfirmReset(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <button className="btn block" disabled={!!busy} onClick={() => setConfirmReset(true)}>Clear backend</button>
      )}

      <h5>Send an order</h5>
      {SAMPLES.map((s) => (
        <button key={s.label} className="btn block" disabled={!!busy} onClick={() => send(s)}>
          {s.label}
        </button>
      ))}

      <h5>Stock</h5>
      {state.stockError && <div className="error">{state.stockError}</div>}
      <input className="filter" type="search" placeholder="Filter stock…" value={filter} onChange={(e) => setFilter(e.target.value)} />
      <ul className="stock">
        {items.map((s) => {
          const out = s.on_hand <= 0
          return (
            <li key={s.id} className={out ? 'out' : ''}>
              <span>
                <b>{s.name}</b>
                <small className="muted"> {s.on_hand} {s.unit}{s.available !== s.on_hand ? ` (${s.available} avail.)` : ''}</small>
              </span>
              {out && s.initial_on_hand > 0
                ? <button className="btn small" disabled={!!busy} onClick={() => restock(s)}>Restock</button>
                : <button className="btn small" disabled={out || !!busy} onClick={() => markOut(s)}>{out ? 'Out' : 'Mark out'}</button>}
            </li>
          )
        })}
      </ul>

      <h5>Activity</h5>
      <ul className="log">
        {state.log.length === 0 && <li className="muted">Nothing yet</li>}
        {state.log.map((l, i) => <li key={i}>{l.text}</li>)}
      </ul>
    </aside>
  )
}
