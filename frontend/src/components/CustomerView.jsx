import { useEffect, useState } from 'react'
import { useStore } from '../useStore.js'
import { api } from '../api.js'
import { ORDER_STATUS, countdown, describeOp, money, priceChange, questionText, restrictionText } from '../logic/format.js'
import ModChip from './ModChip.jsx'
import SubstitutionCard from './SubstitutionCard.jsx'
import Icon from './Icon.jsx'

// Placeholder art for dishes (the menu API has no photos).
function dishEmoji(name = '') {
  const n = name.toLowerCase()
  if (n.includes('noodle')) return '🍜'
  if (n.includes('wrap')) return '🌯'
  if (n.includes('soup')) return '🍲'
  if (n.includes('bowl')) return '🥗'
  return '🍽️'
}

// Dish photo from public/dishes/<id>.jpg; falls back to the emoji tile if the file is missing.
function DishArt({ dish, className }) {
  const [failed, setFailed] = useState(false)
  return (
    <div className={className} aria-hidden="true">
      {failed
        ? <span>{dishEmoji(dish.name)}</span>
        : <img src={`/dishes/${dish.id}.jpg`} alt="" loading="lazy" onError={() => setFailed(true)} />}
    </div>
  )
}

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
        <li key={i}>⛔ <b>{u.label}</b>: We can't offer this option right now.</li>
      ))}
    </ul>
  )
}

// Paid extras on their own lines: the customer keeps or drops each one before choosing.
function ExtrasList({ extras, declined, onToggle, locked }) {
  if (!extras?.length) return null
  return (
    <div className="sub extras">
      <strong>Your extras</strong>
      <ul className="extras-list">
        {extras.map((x) => (
          <li key={x.id}>
            <label className="row between">
              <span>
                <input type="checkbox" checked={!declined.includes(x.id)} disabled={locked}
                  onChange={() => onToggle(x.id)} />{' '}
                Extra {x.name}{x.qty > 1 ? ` ×${x.qty}` : ''}
              </span>
              <span className={declined.includes(x.id) ? 'muted strike' : ''}>+{money(x.price_cents)}</span>
            </label>
          </li>
        ))}
      </ul>
      {!locked && <small className="muted">Untick any extra you don't want. Option prices below don't include extras.</small>}
    </div>
  )
}

export default function CustomerView({ ordersFocus = 0 }) {
  const { state, dispatch } = useStore()
  const { menu, menuError } = state
  const [dishId, setDishId] = useState(null)
  const [qty, setQty] = useState(1)
  const [note, setNote] = useState('')
  const [customerName, setCustomerName] = useState('')
  const [evaluation, setEvaluation] = useState(null)
  const [clarificationHistory, setClarificationHistory] = useState([])
  const [reply, setReply] = useState('')
  // Unticked extras, tied to the evaluation they were chosen on (a new one starts with all kept).
  const [declinedState, setDeclinedState] = useState({ key: '', ids: [] })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [flash, setFlash] = useState(null)
  const [dismissedRejections, setDismissedRejections] = useState([])
  const [query, setQuery] = useState('')
  const [avoid, setAvoid] = useState(null)

  const selectedId = dishId ?? menu[0]?.id
  const dish = menu.find((d) => d.id === selectedId)
  const evalDish = evaluation ? menu.find((d) => d.id === evaluation.dish?.id) : null
  const allergens = [...new Set(menu.flatMap((m) => m.allergens ?? []))].sort()
  const q = query.trim().toLowerCase()
  const shown = menu.filter((m) => (!avoid || !(m.allergens ?? []).includes(avoid))
    && (!q || m.name.toLowerCase().includes(q) || m.components.some((c) => c.name.toLowerCase().includes(q))))
  const mine = state.orders.filter((o) => state.myOrderIds.includes(o.order_id))

  const evaluationKey = evaluation ? `${evaluation.order_id}:${evaluation.status}` : ''
  const declinedExtras = declinedState.key === evaluationKey ? declinedState.ids : []
  const toggleExtra = (id) => setDeclinedState({
    key: evaluationKey,
    ids: declinedExtras.includes(id) ? declinedExtras.filter((x) => x !== id) : [...declinedExtras, id],
  })
  const keptExtrasCents = (evaluation?.extras ?? []).filter((x) => !declinedExtras.includes(x.id))
    .reduce((sum, x) => sum + x.price_cents, 0)

  useEffect(() => {
    if (ordersFocus) document.getElementById('your-orders')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [ordersFocus])

  const submit = async (clarifications = []) => {
    if (!selectedId) return
    setBusy(true)
    setError(null)
    setFlash(null)
    try {
      const e = await api.evaluate({ dishId: selectedId, qty, text: note, clarifications, channel: 'doordash', customerName: customerName.trim() })
      setEvaluation(e)
      setClarificationHistory(clarifications)
      setReply('')
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

  const sendAnswers = (answer = reply.trim()) => {
    const question = (evaluation.questions ?? []).map(questionText).join('\n') || evaluation.customer_message
    submit([...clarificationHistory, { question, answer }])
  }

  const accept = async (optionId) => {
    setBusy(true)
    setError(null)
    try {
      const order = await api.accept(evaluation.order_id, optionId,
        evaluation.staff_approved_choice ? [] : declinedExtras)
      dispatch({ type: 'ORDER', order })
      dispatch({ type: 'LOG', text: `${order.order_id}: customer accepted ${optionId ?? 'as requested'} → ${order.status}` })
      setFlash(order.status === 'confirmed'
        ? `Order ${order.order_id} confirmed. The kitchen has it.`
        : `Order ${order.order_id} sent. The kitchen is confirming it.`)
      setEvaluation(null)
      setNote('')
      setClarificationHistory([])
      setReply('')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const startOver = () => {
    if (evaluation?.clarification_exhausted) setNote('')
    setEvaluation(null)
    setError(null)
    setFlash(null)
    setClarificationHistory([])
    setReply('')
  }

  const reviewOrder = (order) => {
    setDishId(order.dish.id)
    setQty(order.qty)
    setNote(order.original_text ?? '')
    setCustomerName(order.customer_name ?? '')
    setClarificationHistory(order.evaluation.clarifications ?? [])
    setReply('')
    setError(null)
    setFlash(null)
    setEvaluation(order.evaluation)
  }

  const chooseSomethingElse = (order) => {
    setEvaluation(null)
    setDishId(order.dish.id)
    setQty(order.qty)
    // Keep the original restriction in the new request for parsing and staff review.
    setNote([order.original_text, ...(order.evaluation.clarifications ?? []).map((c) => c.answer)].filter(Boolean).join('\n'))
    setCustomerName(order.customer_name ?? '')
    setClarificationHistory([])
    setReply('')
    setError(null)
    setFlash('Choose another dish or edit your request above. Your original request is kept so any allergy or dietary restriction can be reviewed again.')
  }

  const total = dish ? dish.price_cents * qty : 0
  const questions = evaluation?.questions ?? []
  const canReply = evaluation && ['needs_clarification', 'unavailable'].includes(evaluation.status)
    && !evaluation.clarification_exhausted
  // "Sorry, we don't have X… something else instead, or leave it out?" gets one-tap answers.
  const canLeaveOut = canReply && (evaluation.questions ?? []).some((q) => /leave it out/i.test(questionText(q)))
  const clarificationLimit = evaluation?.clarification_limit ?? 3

  return (
    <div className="shop">
      <section className="shop-main">
        <div className="toolbar">
          <label className="search">
            <Icon name="search" />
            <input type="search" placeholder="Search dishes or ingredients" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search the menu" />
          </label>
        </div>

        <h1 className="page-title">Menu</h1>

        {allergens.length > 0 && (
          <div className="pills" role="group" aria-label="Filter by allergen">
            <button className={`pill-tab ${avoid == null ? 'on' : ''}`} aria-pressed={avoid == null} onClick={() => setAvoid(null)}>All dishes</button>
            {allergens.map((a) => (
              <button key={a} className={`pill-tab ${avoid === a ? 'on' : ''}`} aria-pressed={avoid === a} onClick={() => setAvoid(avoid === a ? null : a)}>
                No {a}
              </button>
            ))}
          </div>
        )}

        {menuError && <div className="error">{menuError}</div>}
        {!menuError && menu.length === 0 && <p className="muted">Loading menu…</p>}
        {menu.length > 0 && shown.length === 0 && <p className="empty">No dishes match. Clear the search or pick another filter.</p>}

        <div className="dish-grid">
          {shown.map((m) => {
            const on = selectedId === m.id
            return (
              <article key={m.id} className={`dish ${on ? 'selected' : ''}`}>
                <DishArt dish={m} className="dish-art" />
                <div className="dish-head">
                  <h3>{m.name}</h3>
                  <span className="dish-price">{money(m.price_cents)}</span>
                </div>
                <p className="dish-desc">{m.components.map((c) => c.name).join(', ')}</p>
                {m.allergens?.length > 0 && (
                  <div className="dish-tags" aria-label="Contains">
                    {m.allergens.map((a) => <span key={a} className="tag-pill">{a}</span>)}
                  </div>
                )}
                <button className={`btn ${on ? 'chosen' : 'cta'} block`} disabled={!!evaluation} aria-pressed={on} onClick={() => setDishId(m.id)}>
                  {on ? <><Icon name="check" size={18} /> In your order</> : 'Add to order'}
                </button>
              </article>
            )
          })}
        </div>

          <section id="your-orders" className="section">
            <h2>Your orders <span className="count">{mine.length}</span></h2>
            {mine.length === 0 && <p className="empty">No orders yet. Pick a dish from the menu and add your request to start one.</p>}
            <div className="orders-list">
            {mine.map((o) => {
              const [label, tone] = ORDER_STATUS[o.status] ?? [o.status, 'info']
              const rejectNote = o.staff_note ?? o.history?.filter((h) => h.event === 'rejected').at(-1)?.note
              const waiting = o.status === 'awaiting_customer' || o.status === 'awaiting_staff'
              return (
                <div key={o.order_id} className="my-order card">
                  <div className="row between">
                    <b>{o.order_id} · {o.qty > 1 ? `${o.qty}× ` : ''}{o.dish?.name}</b>
                    <span className={`status ${tone}`}>{label}</span>
                  </div>
                  {o.original_text && <div className="muted">“{o.original_text}”</div>}
                  {o.evaluation?.clarifications?.length > 0 && (
                    <details>
                      <summary>Your updates</summary>
                      <ol>{o.evaluation.clarifications.map((item, i) => <li key={i}>{item.answer}</li>)}</ol>
                    </details>
                  )}
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
                    <div className="row"><button className="btn small" onClick={() => reviewOrder(o)}>Review options</button></div>
                  )}
                  {o.status === 'confirmed' && <Instructions lines={o.kitchen_instructions} title="Your order as the kitchen will make it" />}
                  {o.status === 'awaiting_customer' && o.staff_approved_choice && <p>Staff have reviewed your request. Please review the final option and price, then confirm if you're happy with it.</p>}
                  {o.staff_approved_choice && o.staff_note && <p>Staff note: {o.staff_note}</p>}
                  {o.status === 'rejected' && (
                    <>
                      <p className="danger">The kitchen couldn't approve this request{rejectNote ? `: ${rejectNote.replace(/\.+$/, '')}.` : '.'} You won't be charged.</p>
                      {!dismissedRejections.includes(o.order_id) && (
                        <div>
                          <p>Would you like something else instead?</p>
                          <div className="row">
                            <button className="btn small" onClick={() => chooseSomethingElse(o)}>Choose something else</button>
                            <button className="btn small ghost" onClick={() => setDismissedRejections([...dismissedRejections, o.order_id])}>No thanks</button>
                          </div>
                        </div>
                      )}
                    </>
                  )}
                  {o.status === 'expired' && <p className="muted">This order wasn't confirmed in time, so it was cancelled.</p>}
                </div>
              )
            })}
            </div>
          </section>
      </section>

      <aside className="cart">
        <h2>Order summary</h2>
        <div className="channel-seg"><Icon name="truck" /> DoorDash delivery</div>

        {dish && (
          <div className="line-item">
            <DishArt key={dish.id} dish={dish} className="line-art" />
            <div className="line-text">
              <b>{dish.name}</b>
              <span className="muted">{money(dish.price_cents)}</span>
            </div>
            <div className="stepper" role="group" aria-label="Quantity">
              <button aria-label="One fewer" disabled={!!evaluation || qty <= 1} onClick={() => setQty(Math.max(1, qty - 1))}><Icon name="minus" size={16} /></button>
              <input type="number" min={1} max={20} value={qty} disabled={!!evaluation} aria-label="Quantity"
                onChange={(e) => setQty(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
              <button aria-label="One more" disabled={!!evaluation || qty >= 20} onClick={() => setQty(Math.min(20, qty + 1))}><Icon name="plus" size={16} /></button>
            </div>
          </div>
        )}
        {dish?.allergens?.length > 0 && <p className="muted">Contains: {dish.allergens.join(', ')}</p>}

        <label className="field">
          <span>Name (optional)</span>
          <input type="text" value={customerName} disabled={!!evaluation} onChange={(e) => setCustomerName(e.target.value)} placeholder="e.g. Sam" />
        </label>

        <label className="field">
          <span>Special request</span>
          <textarea rows={3} value={note} disabled={!!evaluation} onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. no pickled onion, swap chicken for tofu, sauce on the side, I'm allergic to peanuts" />
        </label>

        <dl className="totals">
          <div><dt>Menu price{qty > 1 ? ` × ${qty}` : ''}</dt><dd>{money(total)}</dd></div>
          <div className="grand"><dt>Starting total</dt><dd>{money(total)}</dd></div>
        </dl>
        <p className="fine">Extras and premium swaps are added after the kitchen checks your request.</p>

        {!evaluation && (
          <button className="btn cta block big" disabled={busy || !dish} onClick={() => submit()}>
            {busy ? 'Checking with the kitchen…' : 'Check my order'}
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
                <ul className="why-list">{questions.map((q, i) => <li key={i}>{questionText(q)}</li>)}</ul>
              </div>
            )}

            {evaluation.status === 'ready' && (
              <div className="sub sub-done">
                <strong>{evaluation.customer_message || 'We can make this as you asked.'}</strong>
                <ExtrasList extras={evaluation.extras} declined={declinedExtras} onToggle={toggleExtra}
                  locked={Boolean(evaluation.staff_approved_choice)} />
                <div className="price">{priceChange(
                  evaluation.extras?.length && !evaluation.staff_approved_choice
                    ? (evaluation.as_requested?.own_price_change_cents ?? 0) + keptExtrasCents
                    : evaluation.as_requested?.price_change_cents)}</div>
                <Instructions lines={evaluation.as_requested?.kitchen_instructions} />
                <div className="row">
                  <button className="btn primary" disabled={busy} onClick={() => accept(null)}>
                    {evaluation.review?.required && !evaluation.staff_approved_choice ? 'Send to staff for review' : 'Confirm order'}
                  </button>
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
                <ExtrasList extras={evaluation.extras} declined={declinedExtras} onToggle={toggleExtra}
                  locked={Boolean(evaluation.staff_approved_choice)} />
                {evaluation.options.filter((o) => !evaluation.staff_approved_choice || o.option_id === evaluation.staff_approved_choice).map((o) => (
                  <SubstitutionCard key={o.option_id} busy={busy} audience="customer"
                    option={evaluation.staff_approved_choice ? o : { ...o, price_change_cents: o.own_price_change_cents ?? o.price_change_cents }}
                    recommended={o.option_id === evaluation.recommended_option_id}
                    onChoose={accept} chooseLabel={evaluation.staff_approved_choice ? 'Confirm this option and price' : evaluation.review?.required ? 'Choose and send to staff' : 'Choose this'} />
                ))}
                {!evaluation.staff_approved_choice && <UnavailableList items={evaluation.unavailable_options} />}
                <div className="row">
                  {evaluation.as_requested?.feasible && (!evaluation.staff_approved_choice || evaluation.staff_approved_choice === 'as_requested') && (
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
                {evaluation.clarification_exhausted && (
                  <div className="row"><button className="btn primary" onClick={startOver}>Start a new request</button></div>
                )}
              </div>
            )}

            {canReply && (
              <div className="sub sub-pending">
                {clarificationHistory.length > 0 && (
                  <details>
                    <summary>Your replies</summary>
                    <ul>{clarificationHistory.map((item, i) => <li key={i}>{item.answer}</li>)}</ul>
                  </details>
                )}
                {canLeaveOut && (
                  <div className="row">
                    <button className="btn" disabled={busy} onClick={() => sendAnswers('leave it out')}>Leave it out</button>
                    <button className="btn ghost" disabled={busy} onClick={startOver}>Cancel this request</button>
                  </div>
                )}
                <label className="field">
                  <span>{canLeaveOut ? 'Or tell us what you would like instead' : 'What would you like instead?'}</span>
                  <textarea rows={2} value={reply} disabled={busy} onChange={(e) => setReply(e.target.value)}
                    placeholder="e.g. add avocado instead, or swap chicken for tofu" />
                </label>
                <p className="muted">Answer the questions or revise the change. We'll check it with the kitchen again.</p>
                <small className="muted">{Math.max(0, clarificationLimit - clarificationHistory.length)} replies remaining.</small>
                <div className="row">
                  <button className="btn primary" disabled={busy || !reply.trim()} onClick={() => sendAnswers()}>
                    {busy ? 'Checking with the kitchen…' : 'Check updated request'}
                  </button>
                  <button className="btn ghost" disabled={busy} onClick={startOver}>Edit original request</button>
                </div>
              </div>
            )}
          </section>
        )}
      </aside>
    </div>
  )
}
