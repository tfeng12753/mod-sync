import { useState } from 'react'
import { useStore } from '../useStore.js'
import { api } from '../api.js'
import { ORDER_STATUS, countdown, describeOp, money, priceChange, questionText, restrictionText } from '../logic/format.js'
import ModChip from './ModChip.jsx'
import SubstitutionCard from './SubstitutionCard.jsx'

const CHANNELS = [
  ['doordash', 'DoorDash'],
  ['ubereats', 'Uber Eats'],
  ['walk-in', 'Walk-in'],
  ['phone', 'Phone'],
]

function Instructions({ lines, title = 'What the kitchen will see' }) {
  if (!lines?.length) return null
  return (
    <details className="ki-preview">
      <summary>{title}</summary>
      <ul className="instructions">{lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
    </details>
  )
}

function Understood({ evaluation, dish }) {
  const { ops = [], restrictions = [] } = evaluation.parsed ?? {}
  if (evaluation.status === 'needs_clarification' && ops.length === 0 && restrictions.length === 0) return null
  return (
    <div className="preview">
      <b>Here's how we understood your order</b>
      {ops.length === 0 && restrictions.length === 0 && <p className="muted">No changes: made as listed.</p>}
      {ops.length > 0 && <div className="chips">{ops.map((o, i) => <ModChip key={i} chip={describeOp(o, dish)} />)}</div>}
      {restrictions.length > 0 && <div className="allergy-banner">⚠ {restrictions.map(restrictionText).join(' · ')}</div>}
    </div>
  )
}

function UnavailableList({ items }) {
  if (!items?.length) return null
  return (
    <ul className="blocked">
      {items.map((u, i) => (
        <li key={i}>⛔ <b>{u.label}</b>{u.reasons?.length > 0 && `: ${u.reasons.join(' ')}`}</li>
      ))}
    </ul>
  )
}

export default function CustomerView() {
  const { state, dispatch } = useStore()
  const { menu, menuError } = state
  const [dishId, setDishId] = useState(null)
  const [qty, setQty] = useState(1)
  const [note, setNote] = useState('')
  const [channel, setChannel] = useState('doordash')
  const [customerName, setCustomerName] = useState('')
  const [evaluation, setEvaluation] = useState(null)
  const [answers, setAnswers] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [flash, setFlash] = useState(null)

  const selectedId = dishId ?? menu[0]?.id
  const dish = menu.find((d) => d.id === selectedId)
  const evalDish = evaluation ? menu.find((d) => d.id === evaluation.dish?.id) : null
  const mine = state.orders.filter((o) => state.myOrderIds.includes(o.order_id))

  const submit = async (clarifications) => {
    if (!selectedId) return
    setBusy(true)
    setError(null)
    setFlash(null)
    try {
      const e = await api.evaluate({ dishId: selectedId, qty, text: note, clarifications, channel, customerName: customerName.trim() })
      setEvaluation(e)
      setAnswers({})
      if (e.order_id) {
        dispatch({ type: 'MINE', orderId: e.order_id })
        dispatch({ type: 'LOG', text: `${e.order_id}: ${e.dish?.name} → ${e.status}` })
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const sendAnswers = () => {
    const clarifications = (evaluation.questions ?? []).map((q, i) => ({ question: questionText(q), answer: (answers[i] ?? '').trim() }))
    submit(clarifications)
  }

  const accept = async (optionId) => {
    setBusy(true)
    setError(null)
    try {
      const order = await api.accept(evaluation.order_id, optionId)
      dispatch({ type: 'ORDER', order })
      dispatch({ type: 'LOG', text: `${order.order_id}: customer accepted ${optionId ?? 'as requested'} → ${order.status}` })
      setFlash(order.status === 'confirmed'
        ? `Order ${order.order_id} confirmed. The kitchen has it.`
        : `Order ${order.order_id} sent. The kitchen is confirming it.`)
      setEvaluation(null)
      setNote('')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const startOver = () => {
    setEvaluation(null)
    setError(null)
  }

  const total = dish ? dish.price_cents * qty : 0
  const questions = evaluation?.questions ?? []
  const allAnswered = questions.every((_, i) => (answers[i] ?? '').trim())

  return (
    <div className="phone">
      <h2>Order</h2>

      {menuError && <div className="error">{menuError}</div>}
      {!menuError && menu.length === 0 && <p className="muted">Loading menu…</p>}

      <div className="menu">
        {menu.map((m) => (
          <button key={m.id} className={`menu-item ${selectedId === m.id ? 'selected' : ''}`} disabled={!!evaluation}
            onClick={() => setDishId(m.id)}>
            <span className="menu-text">
              <b>{m.name}</b>
              <small className="muted">{m.components.map((c) => c.name).join(', ')}</small>
            </span>
            <span>{money(m.price_cents)}</span>
          </button>
        ))}
      </div>

      {dish?.allergens?.length > 0 && <p className="muted">Contains: {dish.allergens.join(', ')}</p>}

      <div className="row">
        <label className="field inline">
          <span>Qty</span>
          <input type="number" min={1} max={20} value={qty} disabled={!!evaluation}
            onChange={(e) => setQty(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
        </label>
        <label className="field inline grow">
          <span>Name (optional)</span>
          <input type="text" value={customerName} disabled={!!evaluation} onChange={(e) => setCustomerName(e.target.value)} placeholder="e.g. Sam" />
        </label>
        <label className="field inline">
          <span>Channel</span>
          <select value={channel} disabled={!!evaluation} onChange={(e) => setChannel(e.target.value)}>
            {CHANNELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
      </div>

      <label className="field">
        <span>Special request</span>
        <textarea rows={3} value={note} disabled={!!evaluation} onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. no pickled onion, swap chicken for tofu, sauce on the side, I'm allergic to peanuts" />
      </label>

      {!evaluation && (
        <button className="btn primary block" disabled={busy || !dish} onClick={() => submit()}>
          {busy ? 'Checking with the kitchen…' : `Check my order · ${money(total)}`}
        </button>
      )}

      {error && <div className="error">{error}</div>}
      {flash && <div className="flash">{flash}</div>}

      {evaluation && (
        <section className="result">
          <Understood evaluation={evaluation} dish={evalDish} />

          {evaluation.status === 'needs_clarification' && (
            <div className="sub sub-pending">
              <strong>{evaluation.customer_message || 'A quick question first'}</strong>
              {questions.map((q, i) => (
                <label key={i} className="field">
                  <span>{questionText(q)}</span>
                  <input type="text" value={answers[i] ?? ''} onChange={(e) => setAnswers({ ...answers, [i]: e.target.value })} />
                </label>
              ))}
              <div className="row">
                <button className="btn primary" disabled={busy || !allAnswered} onClick={sendAnswers}>{busy ? 'Checking…' : 'Send answers'}</button>
                <button className="btn ghost" disabled={busy} onClick={startOver}>Edit request</button>
              </div>
            </div>
          )}

          {evaluation.status === 'ready' && (
            <div className="sub sub-done">
              <strong>{evaluation.customer_message || 'We can make this as you asked.'}</strong>
              <div className="price">{priceChange(evaluation.as_requested?.price_change_cents)}</div>
              <Instructions lines={evaluation.as_requested?.kitchen_instructions} />
              <div className="row">
                <button className="btn primary" disabled={busy} onClick={() => accept(null)}>Confirm order</button>
                <button className="btn ghost" disabled={busy} onClick={startOver}>Edit request</button>
              </div>
              {evaluation.expires_at && <small className="muted">We'll hold this for {countdown(evaluation.expires_at, state.now)}.</small>}
            </div>
          )}

          {evaluation.status === 'needs_customer_approval' && (
            <div>
              <div className="sub sub-pending">
                <strong>{evaluation.customer_message || "We can't make it exactly as asked."}</strong>
                {evaluation.as_requested?.reasons?.length > 0 && (
                  <ul className="why-list">{evaluation.as_requested.reasons.map((r, i) => <li key={i} className="why">{r}</li>)}</ul>
                )}
                {evaluation.expires_at && <small className="muted">Options held for {countdown(evaluation.expires_at, state.now)}.</small>}
              </div>
              {evaluation.options.map((o) => (
                <SubstitutionCard key={o.option_id} option={o} audience="customer" busy={busy}
                  recommended={o.option_id === evaluation.recommended_option_id}
                  onChoose={accept} chooseLabel="Choose this" />
              ))}
              <UnavailableList items={evaluation.unavailable_options} />
              <div className="row">
                {evaluation.as_requested?.feasible && (
                  <button className="btn" disabled={busy} onClick={() => accept(null)}>Keep it as I asked</button>
                )}
                <button className="btn ghost" disabled={busy} onClick={startOver}>Edit request</button>
              </div>
            </div>
          )}

          {evaluation.status === 'needs_staff_review' && (
            <div>
              <div className="review">
                <b>The kitchen is confirming your request</b>
                <p>{evaluation.customer_message}</p>
                <small className="muted">Staff check every allergy and dietary request before it is made. You'll see the result under “Your orders”.</small>
              </div>
              {evaluation.options.filter((o) => o.option_id === evaluation.recommended_option_id).map((o) => (
                <SubstitutionCard key={o.option_id} option={o} audience="customer" recommended />
              ))}
              <div className="row">
                <button className="btn" onClick={startOver}>Done</button>
              </div>
            </div>
          )}

          {evaluation.status === 'unavailable' && (
            <div className="sub sub-unavailable">
              <strong>{evaluation.customer_message || "Sorry, we can't make that right now."}</strong>
              {evaluation.as_requested?.reasons?.length > 0 && (
                <ul className="why-list">{evaluation.as_requested.reasons.map((r, i) => <li key={i} className="why">{r}</li>)}</ul>
              )}
              <UnavailableList items={evaluation.unavailable_options} />
              <div className="row"><button className="btn primary" onClick={startOver}>Edit request</button></div>
            </div>
          )}
        </section>
      )}

      {mine.length > 0 && <h3>Your orders</h3>}
      {mine.map((o) => {
        const [label, tone] = ORDER_STATUS[o.status] ?? [o.status, 'info']
        const rejectNote = o.staff_note ?? o.history?.filter((h) => h.event === 'rejected').at(-1)?.note
        const waiting = o.status === 'awaiting_customer' || o.status === 'awaiting_staff'
        return (
          <div key={o.order_id} className="my-order">
            <div className="row between">
              <b>{o.order_id} · {o.qty > 1 ? `${o.qty}× ` : ''}{o.dish?.name}</b>
              <span className={`status ${tone}`}>{label}</span>
            </div>
            {o.original_text && <div className="muted">“{o.original_text}”</div>}
            {waiting && o.expires_at && <small className="muted">Expires in {countdown(o.expires_at, state.now)}</small>}
            {waiting && (
              <div className="row">
                <button className="btn small" onClick={() => api.cancel(o.order_id)
                  .then(() => dispatch({ type: 'LOG', text: `${o.order_id} cancelled by customer` }))
                  .catch((e) => dispatch({ type: 'LOG', text: `Cancel failed: ${e.message}` }))}>Cancel order</button>
              </div>
            )}
            {o.status === 'cancelled' && <p className="muted">You cancelled this order. You won't be charged.</p>}
            {o.status === 'awaiting_customer' && evaluation?.order_id !== o.order_id && (
              <div className="row"><button className="btn small" onClick={() => setEvaluation(o.evaluation)}>Review options</button></div>
            )}
            {o.status === 'confirmed' && <Instructions lines={o.kitchen_instructions} title="Your order as the kitchen will make it" />}
            {o.status === 'rejected' && <p className="danger">The kitchen couldn't make this safely{rejectNote ? `: ${rejectNote}` : '.'} You won't be charged.</p>}
            {o.status === 'expired' && <p className="muted">This order wasn't confirmed in time, so it was cancelled.</p>}
          </div>
        )
      })}
    </div>
  )
}
