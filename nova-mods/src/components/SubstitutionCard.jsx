import { acceptResponse, describeMod, ingName, subToMod } from '../logic/order.js'

function secondsLeft(sub, now) {
  return Math.max(0, Math.ceil((sub.deadline - now) / 1000))
}

function statusText(sub) {
  switch (sub.status) {
    case 'staff_review': return 'waiting for kitchen decision'
    case 'approved': return sub.reason === 'requested' && sub.chosenId === sub.requestedId ? 'Kitchen confirmed the request' : 'Customer approved'
    case 'kept_original': return 'Customer OK’d: dish stays as on the menu'
    case 'no_reply': return 'No reply: call the customer'
    case 'item_removed': return 'Item removed from the order (refund)'
    default: return sub.status
  }
}

// Plain-English version of what the customer asked for
function askedText(sub) {
  const n = ingName(sub.missing)
  if (sub.modType === 'remove') return `no ${n}`
  if (sub.modType === 'add') return `extra ${n}`
  if (sub.modType === 'side') return `${n} on the side`
  return `${n} → ${ingName(sub.requestedId)}`
}

function titleFor(sub, audience) {
  if (sub.reason === 'requested') {
    return audience === 'customer' ? `You asked for: ${askedText(sub)}` : `Customer request: ${describeMod(subToMod(sub))}`
  }
  return sub.reason === 'allergen'
    ? `${ingName(sub.missing)} conflicts with the allergy`
    : `${ingName(sub.missing)} is out of stock`
}

// What the customer is being asked, in plain words
function questionText(sub) {
  const n = ingName(sub.missing)
  const top = sub.options[0]
  if (sub.offer === 'replacement') {
    if (sub.reason !== 'requested') return <>The kitchen can swap to <b>{top.name}</b> - {top.reason}. No extra charge.</>
    if (sub.modType === 'remove') return <>The kitchen can't leave out the {n.toLowerCase()}, but can replace it with <b>{top.name}</b> - {top.reason}. No extra charge.</>
    return <>The kitchen can't do {ingName(sub.requestedId)}, but can offer <b>{top.name}</b> instead - {top.reason}. No extra charge.</>
  }
  if (sub.offer === 'as_is') {
    if (sub.modType === 'add') return <>The kitchen can't add extra {n.toLowerCase()}. Your meal would be made without it.</>
    if (sub.modType === 'side') return <>The kitchen can't serve the {n.toLowerCase()} on the side. It would come on the food.</>
    return <>The kitchen can't swap the {n.toLowerCase()} for {ingName(sub.requestedId).toLowerCase()}. You'd keep the original.</>
  }
  // last resort
  return sub.reason === 'requested'
    ? <>The kitchen can't leave out the {n.toLowerCase()} and has no replacement to offer. The last option is to take this item off your order and refund you.</>
    : <>We have no safe replacement for the {n.toLowerCase()}. The last option is to take this item off your order and refund you.</>
}

// What the customer is told once it's settled
function resultText(sub) {
  const n = ingName(sub.missing)
  if (sub.status === 'item_removed') return 'This item was removed from your order. You will be refunded.'
  if (sub.status === 'no_reply') return "We couldn't reach you. The restaurant will call you before making this item."
  if (sub.status === 'kept_original') {
    if (sub.modType === 'add') return `OK: your meal is made without extra ${n.toLowerCase()}.`
    if (sub.modType === 'side') return `OK: the ${n.toLowerCase()} comes on the food.`
    return `OK: you'll get the original ${n.toLowerCase()}.`
  }
  // approved
  if (sub.reason !== 'requested') return `Swapped to ${ingName(sub.chosenId)}.`
  if (sub.modType === 'swap') return `Confirmed: ${ingName(sub.chosenId)}.`
  if (sub.modType === 'remove' && sub.chosenId !== sub.requestedId) return `Replaced the ${n.toLowerCase()} with ${ingName(sub.chosenId)}.`
  return `Confirmed: ${askedText(sub)}.`
}

export default function SubstitutionCard({ sub, order, now, audience, dispatch }) {
  const pending = sub.status === 'pending'
  const inQueue = sub.status === 'staff_review'
  const requested = sub.reason === 'requested'
  const open = inQueue || pending || sub.status === 'no_reply'
  const respond = (response, chosenId) => dispatch({ type: 'SUB_RESPOND', orderId: order.id, subId: sub.id, response, chosenId })

  if (audience === 'customer') {
    return (
      <div className={`sub ${open ? 'sub-pending' : 'sub-done'} ${sub.status === 'item_removed' ? 'sub-removed' : ''}`}>
        <strong>{titleFor(sub, audience)}</strong>
        {inQueue && <p>{requested ? "We're checking with the kitchen that they can do that." : "We're checking with the kitchen about a swap. We'll ask you in a moment."}</p>}

        {pending && (
          <>
            <p>{questionText(sub)}</p>
            <div className="row wrap">
              {sub.offer === 'replacement' &&
                sub.options.slice(0, 2).map((o, i) => (
                  <button key={o.id} className={i === 0 ? 'btn primary' : 'btn'} onClick={() => respond('approve', o.id)}>
                    {sub.modType === 'remove' ? 'Replace with' : 'Swap to'} {o.name}
                  </button>
                ))}
              {sub.offer === 'replacement' && requested && sub.modType === 'swap' && (
                <button className="btn" onClick={() => respond('keep')}>Keep original {ingName(sub.missing)}</button>
              )}
              {sub.offer === 'as_is' && <button className="btn primary" onClick={() => respond('keep')}>That's fine</button>}
              <button className={sub.offer === 'remove_item' ? 'btn primary' : 'btn ghost'} onClick={() => respond('remove')}>
                {sub.offer === 'remove_item' ? 'OK, remove this item (refund)' : 'Remove this item from my order'}
              </button>
            </div>
            <small>If we don't hear back in {secondsLeft(sub, now)}s the restaurant will call you. Nothing changes until you say so.</small>
          </>
        )}

        {!open && <p>{resultText(sub)}</p>}
        {sub.status === 'no_reply' && <p>{resultText(sub)}</p>}
      </div>
    )
  }

  // staff view (the Possible / Not possible buttons live in the Substitution queue, not here)
  const phone = sub.status === 'no_reply'
  return (
    <div className={`sub ${open ? 'sub-pending' : 'sub-done'} ${sub.status === 'item_removed' ? 'sub-removed' : ''}`}>
      <div className="row between">
        <strong>{titleFor(sub, audience)}</strong>
        <span className={`pill pill-${sub.status}`}>{pending ? `waiting for customer · ${secondsLeft(sub, now)}s` : statusText(sub)}</span>
      </div>
      {sub.warning && <p className="danger">⚠ {sub.warning}</p>}

      {(pending || phone) && (
        <p className="why">
          Customer is being asked:{' '}
          {sub.offer === 'replacement' && <>to approve <b>{sub.options[0]?.name}</b>{requested && sub.modType === 'swap' ? ' (or keep the original)' : ''} - or remove the item</>}
          {sub.offer === 'as_is' && <>whether to continue without it - or remove the item</>}
          {sub.offer === 'remove_item' && <>to approve taking the item off the order (last resort)</>}
        </p>
      )}

      {inQueue && sub.options.length > 0 && !(requested && sub.options[0].id === sub.requestedId && sub.modType !== 'swap') && (
        <ol className="options">
          {sub.options.map((o, i) => (
            <li key={o.id} className={i === 0 ? 'best' : ''}>
              <b>{o.name}</b> {o.score != null && <span className="score">score {o.score}</span>}
              <div className="why">{o.reason}</div>
            </li>
          ))}
        </ol>
      )}
      {sub.rejected?.length > 0 && (
        <ul className="blocked">
          {sub.rejected.map((r) => (
            <li key={r.id}>
              {requested && r.id === sub.requestedId ? <>✕ Request <b>{describeMod(subToMod(sub))}</b> refused by kitchen</> : <>✕ <b>{r.name}</b> ruled out by kitchen</>}
            </li>
          ))}
        </ul>
      )}
      {sub.blocked.length > 0 && (
        <ul className="blocked">
          {sub.blocked.map((b) => <li key={b.id}>⛔ <b>{b.name}</b> blocked: {b.reason}</li>)}
        </ul>
      )}

      {phone && (
        <div className="row wrap">
          {sub.offer !== 'remove_item' && <button className="btn small" onClick={() => respond(acceptResponse(sub))}>📞 Customer agreed</button>}
          <button className="btn small ghost" onClick={() => respond('remove')}>📞 Customer wants the item removed</button>
        </div>
      )}
      {pending && (
        <div className="row wrap">
          <button className="btn small" onClick={() => respond(acceptResponse(sub))}>Demo: customer says yes</button>
          <button className="btn small ghost" onClick={() => respond('remove')}>Demo: customer removes item</button>
        </div>
      )}
    </div>
  )
}
