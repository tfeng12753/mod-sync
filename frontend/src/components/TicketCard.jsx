import { useState } from 'react'
import ModChip from './ModChip.jsx'
import SubstitutionCard from './SubstitutionCard.jsx'
import { api } from '../api.js'
import { ORDER_STATUS, channelClass, countdown, describeOp, packChecklist, prettyId, priceChange, restrictionText } from '../logic/format.js'

function Evidence({ evidence }) {
  if (!evidence) return null
  const present = Object.entries(evidence.allergens_present ?? {})
  return (
    <details className="evidence" open>
      <summary>Allergen evidence</summary>
      {evidence.components?.length > 0 && (
        <table className="ev-table">
          <thead><tr><th>Component</th><th>Allergens</th></tr></thead>
          <tbody>
            {evidence.components.map((c) => (
              <tr key={c.id}>
                <td>{c.name}{c.replaced_from ? <small className="muted"> (replaces {prettyId(c.replaced_from)})</small> : null}</td>
                <td>{c.allergens?.length ? c.allergens.join(', ') : <span className="muted">none listed</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {present.length > 0 && (
        <div className="ev-block">
          <b>Allergens present</b>
          <ul>{present.map(([a, where]) => <li key={a}><b>{a}</b>: {Array.isArray(where) ? where.join('; ') : String(where)}</li>)}</ul>
        </div>
      )}
      {evidence.cross_contact?.length > 0 && (
        <div className="ev-block">
          <b>Cross-contact</b>
          <ul className="blocked">{evidence.cross_contact.map((c, i) => <li key={i}>⚠ {c}</li>)}</ul>
        </div>
      )}
      <p className="disclaimer">
        {evidence.note ? `${evidence.note} ` : ''}The system does not certify any dish as safe or allergen-free; staff make the call.
      </p>
    </details>
  )
}

function ReviewControls({ order, dispatch }) {
  const e = order.evaluation ?? {}
  const current = order.selected_option_id ?? null
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [rejecting, setRejecting] = useState(false)
  const [note, setNote] = useState('')

  const run = async (label, fn) => {
    setBusy(true)
    setError(null)
    try {
      const updated = await fn()
      dispatch({ type: 'ORDER', order: updated })
      dispatch({ type: 'LOG', text: `${order.order_id}: staff ${label} → ${updated.status}` })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const currentLabel = current ? e.options?.find((o) => o.option_id === current)?.label : 'as requested'

  return (
    <>
      <h5>Options</h5>
      {e.as_requested?.feasible && (
        <div className={`sub ${current == null ? 'sub-selected' : 'sub-done'}`}>
          <div className="row between">
            <strong>As requested</strong>
            {current == null && <span className="pill pill-approved">Current</span>}
          </div>
          <div className="price">{priceChange(e.as_requested.price_change_cents)}</div>
          {current != null && (
            <div className="row"><button className="btn small" disabled={busy} onClick={() => run('switched to as requested', () => api.approve(order.order_id, null))}>Approve as requested</button></div>
          )}
        </div>
      )}
      {(e.options ?? []).map((o) => (
        <SubstitutionCard key={o.option_id} option={o} audience="staff" busy={busy}
          recommended={o.option_id === e.recommended_option_id}
          selected={o.option_id === current}
          onChoose={o.option_id === current ? null : (id) => run(`switched to ${id}`, () => api.edit(order.order_id, id))}
          chooseLabel="Switch to this option" />
      ))}
      {e.unavailable_options?.length > 0 && (
        <ul className="blocked">
          {e.unavailable_options.map((u, i) => <li key={i}>⛔ <b>{u.label}</b>{u.reasons?.length ? `: ${u.reasons.join(' ')}` : ''}</li>)}
        </ul>
      )}

      {error && <div className="error">{error}</div>}

      {!rejecting ? (
        <div className="row">
          <button className="btn primary grow" disabled={busy || (current == null && !e.as_requested?.feasible)}
            onClick={() => run(`approved ${current ?? 'as requested'}`, () => api.approve(order.order_id, current))}>
            Approve: {currentLabel ?? current}
          </button>
          <button className="btn danger-btn" disabled={busy} onClick={() => setRejecting(true)}>Reject</button>
        </div>
      ) : (
        <div className="reject-box">
          <label className="field">
            <span>Reason (sent with the rejection)</span>
            <textarea rows={2} value={note} onChange={(ev) => setNote(ev.target.value)} placeholder="e.g. cross-contact risk in the shared wok" />
          </label>
          <div className="row">
            <button className="btn danger-btn" disabled={busy || !note.trim()} onClick={() => run('rejected', () => api.reject(order.order_id, note.trim()))}>Confirm reject</button>
            <button className="btn ghost" disabled={busy} onClick={() => setRejecting(false)}>Cancel</button>
          </div>
        </div>
      )}
    </>
  )
}

export default function TicketCard({ order, dish, now, dispatch, packed = {}, handedOff = false }) {
  const e = order.evaluation ?? {}
  const restrictions = e.parsed?.restrictions ?? []
  const ops = e.parsed?.ops ?? []
  const review = e.review ?? {}
  const [statusLabel, tone] = ORDER_STATUS[order.status] ?? [order.status, 'info']
  const reviewing = order.status === 'awaiting_staff'
  const confirmed = order.status === 'confirmed'
  const closed = order.status === 'rejected' || order.status === 'expired'
  const checklist = confirmed ? packChecklist(order) : []
  const open = checklist.filter((c) => !packed[c.key]).length
  const lastNote = order.history?.at(-1)?.note

  return (
    <article className={`ticket ${closed || handedOff ? 'done' : ''} ${reviewing ? 'attention' : ''}`}>
      <div className="row between">
        <h3>{order.order_id} · {order.qty > 1 ? `${order.qty}× ` : ''}{order.dish?.name}</h3>
        {order.channel && <span className={`channel ${channelClass(order.channel)}`}>{order.channel}</span>}
      </div>
      <div className="row between">
        <span className="muted">{order.customer_name ? `for ${order.customer_name}` : 'no name'}</span>
        <span className={`status ${tone}`}>
          {statusLabel}{(reviewing || order.status === 'awaiting_customer') && order.expires_at ? ` · ${countdown(order.expires_at, now)}` : ''}
        </span>
      </div>

      {order.original_text && <blockquote>“{order.original_text}”</blockquote>}
      {e.clarifications?.length > 0 && (
        <details>
          <summary>Customer updates</summary>
          <ol>{e.clarifications.map((item, i) => <li key={i}>{item.answer}</li>)}</ol>
        </details>
      )}

      {restrictions.length > 0 && (
        <div className="allergy-banner">⚠ {restrictions.map((r) => restrictionText(r).toUpperCase()).join(' · ')}</div>
      )}

      {ops.length > 0 && <div className="chips">{ops.map((o, i) => <ModChip key={i} chip={describeOp(o, dish)} />)}</div>}

      {reviewing && (
        <>
          {(review.reasons?.length > 0 || review.staff_notes?.length > 0) && (
            <div className="review">
              <b>Needs a human check</b>
              {review.reasons?.length > 0 && <ul>{review.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
              {review.staff_notes?.length > 0 && <ul className="notes">{review.staff_notes.map((r, i) => <li key={i}>{r}</li>)}</ul>}
            </div>
          )}
          {e.as_requested?.reasons?.length > 0 && (
            <div className="muted">As asked: {e.as_requested.reasons.join(' ')}</div>
          )}
          <Evidence evidence={review.evidence} />
          <ReviewControls order={order} dispatch={dispatch} />
        </>
      )}

      {order.status === 'awaiting_customer' && (
        <p className="muted">Waiting for the customer to pick an option ({(e.options ?? []).length} offered).</p>
      )}

      {confirmed && !handedOff && (
        <>
          <h5>Order changes</h5>
          <div className="checklist">
            {checklist.map((c) => (
              <label key={c.key} className={`check check-${c.kind}`}>
                <input type="checkbox" checked={!!packed[c.key]} onChange={() => dispatch({ type: 'TOGGLE_PACK', orderId: order.order_id, key: c.key })} />
                <span>{c.label}</span>
              </label>
            ))}
          </div>
          {checklist.length === 0 && <p className="muted">No changes to this order.</p>}
          <button className="btn primary block" disabled={open > 0} onClick={() => dispatch({ type: 'HAND_OFF', orderId: order.order_id })}>
            {open ? 'Not ready for handoff' : 'Hand off to courier'}
          </button>
          {open > 0 && <ul className="stops"><li>{open} order change{open > 1 ? 's' : ''} to check</li></ul>}
        </>
      )}
      {confirmed && handedOff && <div className="muted">✓ Handed off</div>}

      {closed && lastNote && <div className="muted">{order.status === 'rejected' ? 'Reason' : 'Note'}: {lastNote}</div>}
    </article>
  )
}
