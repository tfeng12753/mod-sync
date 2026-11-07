import { ingName } from '../logic/order.js'

function secondsLeft(sub, now) {
  return Math.max(0, Math.ceil((sub.deadline - now) / 1000))
}

const STATUS_TEXT = {
  approved: 'Customer approved',
  fallback: 'No reply: default swap applied',
  declined: 'Customer declined: item left out, credit issued',
  no_safe_option: 'No safe substitute: item left out, credit issued, staff to call',
}

export default function SubstitutionCard({ sub, order, now, audience, dispatch }) {
  const pending = sub.status === 'pending'
  const top = sub.options[0]
  const title =
    sub.reason === 'allergen'
      ? `${ingName(sub.missing)} conflicts with the allergy`
      : `${ingName(sub.missing)} is out of stock`

  if (audience === 'customer') {
    return (
      <div className={`sub ${pending ? 'sub-pending' : 'sub-done'}`}>
        <strong>{title}</strong>
        {pending ? (
          <>
            <p>We suggest <b>{top.name}</b> - {top.reason}. No extra charge.</p>
            <div className="row">
              {sub.options.slice(0, 2).map((o, i) => (
                <button key={o.id} className={i === 0 ? 'btn primary' : 'btn'}
                  onClick={() => dispatch({ type: 'SUB_RESPOND', orderId: order.id, missing: sub.missing, response: 'approve', chosenId: o.id })}>
                  Swap to {o.name}
                </button>
              ))}
              <button className="btn ghost" onClick={() => dispatch({ type: 'SUB_RESPOND', orderId: order.id, missing: sub.missing, response: 'decline' })}>
                No thanks, leave it out
              </button>
            </div>
            <small>If we don't hear back in {secondsLeft(sub, now)}s we'll use {top.name}.</small>
          </>
        ) : (
          <p>{sub.chosenId && ['approved', 'fallback'].includes(sub.status) ? `Swapped to ${ingName(sub.chosenId)}.` : 'Left out, you will be credited.'}</p>
        )}
      </div>
    )
  }

  // staff
  return (
    <div className={`sub ${pending ? 'sub-pending' : 'sub-done'}`}>
      <div className="row between">
        <strong>{title}</strong>
        <span className={`pill pill-${sub.status}`}>{pending ? `waiting for customer · ${secondsLeft(sub, now)}s` : STATUS_TEXT[sub.status]}</span>
      </div>
      {sub.options.length > 0 ? (
        <ol className="options">
          {sub.options.map((o, i) => (
            <li key={o.id} className={i === 0 ? 'best' : ''}>
              <b>{o.name}</b> <span className="score">score {o.score}</span>
              <div className="why">{o.reason}</div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="danger">No safe substitute in stock.</p>
      )}
      {sub.blocked.length > 0 && (
        <ul className="blocked">
          {sub.blocked.map((b) => <li key={b.id}>⛔ <b>{b.name}</b> blocked: {b.reason}</li>)}
        </ul>
      )}
      {pending && (
        <div className="row">
          <button className="btn small" onClick={() => dispatch({ type: 'SUB_RESPOND', orderId: order.id, missing: sub.missing, response: 'approve' })}>Demo: customer approves</button>
          <button className="btn small ghost" onClick={() => dispatch({ type: 'SUB_RESPOND', orderId: order.id, missing: sub.missing, response: 'decline' })}>Demo: customer declines</button>
        </div>
      )}
    </div>
  )
}
