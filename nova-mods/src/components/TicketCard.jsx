import ModChip from './ModChip.jsx'
import SubstitutionCard from './SubstitutionCard.jsx'
import { blockers, itemLabel, packChecklist, finalIngredients, ingName } from '../logic/order.js'

export default function TicketCard({ order, now, dispatch }) {
  const checklist = packChecklist(order)
  const stops = blockers(order)
  const done = order.status === 'handed_off'
  const needsAttention = order.needsReview || order.subs.some((s) => s.status === 'pending')

  return (
    <article className={`ticket ${done ? 'done' : ''} ${needsAttention ? 'attention' : ''}`}>
      <div className="row between">
        <h3>#{order.id} · {itemLabel(order)}</h3>
        <span className={`channel ch-${order.channel.toLowerCase().replace(/[^a-z]/g, '')}`}>{order.channel}</span>
      </div>
      <div className="muted">for {order.name}</div>

      {order.rawNote && <blockquote>“{order.rawNote}”</blockquote>}

      {order.allergens.length > 0 && (
        <div className="allergy-banner">⚠ ALLERGY: {order.allergens.map((a) => a.name.toUpperCase()).join(', ')}</div>
      )}

      {order.mods.length > 0 && <div className="chips">{order.mods.map((m, i) => <ModChip key={i} mod={m} />)}</div>}

      {order.needsReview && (
        <div className="review">
          <b>Needs a human check</b>
          <ul>{order.reviewReasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          <button className="btn small" onClick={() => dispatch({ type: 'RESOLVE_REVIEW', orderId: order.id })}>I've confirmed with the customer</button>
        </div>
      )}

      {order.subs.map((s) => (
        <SubstitutionCard key={s.missing} sub={s} order={order} now={now} audience="staff" dispatch={dispatch} />
      ))}

      {!done && (
        <>
          <details>
            <summary>What goes in the food</summary>
            <p className="muted">{finalIngredients(order).map(ingName).join(' · ')}</p>
          </details>
          <div className="checklist">
            {checklist.map((c) => (
              <label key={c.key} className={`check check-${c.kind}`}>
                <input type="checkbox" checked={!!order.packed[c.key]} onChange={() => dispatch({ type: 'TOGGLE_PACK', orderId: order.id, key: c.key })} />
                <span>{c.label}</span>
              </label>
            ))}
          </div>
          <button className="btn primary block" disabled={stops.length > 0} onClick={() => dispatch({ type: 'HAND_OFF', orderId: order.id })}>
            {stops.length ? 'Not ready for handoff' : 'Hand off to courier'}
          </button>
          {stops.length > 0 && <ul className="stops">{stops.map((s, i) => <li key={i}>{s}</li>)}</ul>}
        </>
      )}
      {done && <div className="muted">✓ Handed off</div>}
    </article>
  )
}
