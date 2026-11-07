import { describeMod, ingName, itemLabel, subToMod } from '../logic/order.js'
import { REQUEST_TYPE_LABELS } from '../store.js'

const RULES = [
  {
    key: 'queueOutOfStock',
    label: 'Out-of-stock swaps',
    on: 'Kitchen approves before the customer is asked.',
    off: 'Sent straight to the customer.',
  },
  {
    key: 'queueAllergen',
    label: 'Allergy-conflict swaps',
    on: 'Kitchen approves before the customer is asked.',
    off: 'Sent straight to the customer. Conflicts are still always caught.',
  },
  {
    key: 'queueRequested',
    label: 'Customer-requested changes',
    on: 'The kinds ticked below wait for the kitchen’s OK.',
    off: 'Applied as the customer asked.',
  },
]

const REASON_TAG = { out_of_stock: 'Out of stock', allergen: 'Allergy', requested: 'Customer request' }

function Toggle({ rule, checked, onChange }) {
  return (
    <label className={`toggle ${checked ? 'is-on' : ''}`}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="switch" aria-hidden="true" />
      <span className="toggle-text">
        <b>{rule.label}</b>
        <small>{checked ? rule.on : rule.off}</small>
      </span>
    </label>
  )
}

// What happens if the kitchen says "not possible" (the customer must approve whatever comes next)
function notPossibleHint(sub, isTheRequest, rest) {
  const n = ingName(sub.missing)
  if (isTheRequest) {
    if (sub.modType === 'swap') return `If not possible, we'll suggest the closest alternative. The customer approves it, keeps the original ${n}, or removes the item.`
    if (sub.modType === 'remove') return `If not possible, we'll suggest a replacement for the ${n}. The customer approves it, or the item comes off the order.`
    if (sub.modType === 'add') return `If not possible, the customer is asked whether to continue without the extra ${n}, or remove the item.`
    return `If not possible, the customer is asked whether the ${n} can come on the food, or remove the item.`
  }
  if (rest.length) return `If not possible, next option: ${rest[0].name}`
  return sub.reason === 'requested' && sub.modType !== 'remove'
    ? `No other options: the customer is asked to keep the original ${n}, or remove the item.`
    : 'No other options: the customer is asked to approve taking the item off the order (last resort).'
}

export default function SubstitutionQueue({ orders, settings, dispatch }) {
  // Oldest first, so nothing sits waiting
  const queue = orders
    .filter((o) => o.status !== 'removed')
    .flatMap((order) => order.subs.filter((s) => s.status === 'staff_review').map((sub) => ({ order, sub })))
    .sort((a, b) => a.order.createdAt - b.order.createdAt)

  const decide = (item, decision) => dispatch({ type: 'STAFF_DECISION', orderId: item.order.id, subId: item.sub.id, decision })
  const anyOn = RULES.some((r) => settings[r.key])

  return (
    <section className={`queue ${queue.length ? 'has-items' : ''}`}>
      <div className="row between">
        <h3>Substitution queue</h3>
        <span className="queue-count">{queue.length ? `${queue.length} waiting for you` : 'All clear'}</span>
      </div>

      <div className="rules">
        <div className="rules-title">What goes through the queue?</div>
        <div className="rules-grid">
          {RULES.map((r) => (
            <div key={r.key}>
              <Toggle rule={r} checked={settings[r.key]} onChange={(value) => dispatch({ type: 'SET_SETTING', key: r.key, value })} />
              {r.key === 'queueRequested' && (
                <div className={`types ${settings.queueRequested ? '' : 'types-off'}`}>
                  {Object.entries(REQUEST_TYPE_LABELS).map(([type, label]) => (
                    <label key={type} className="type-chip">
                      <input
                        type="checkbox"
                        disabled={!settings.queueRequested}
                        checked={settings.requestedTypes[type]}
                        onChange={(e) => dispatch({ type: 'SET_REQUEST_TYPE', modType: type, value: e.target.checked })}
                      />
                      <span>{label}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {queue.length === 0 && (
        <p className="muted">
          {anyOn ? 'Nothing needs a decision right now. Anything for the switched-on rules shows up here.' : 'Every queue rule is off, so nothing waits for the kitchen.'}
        </p>
      )}

      <div className="queue-list">
        {queue.map((item) => {
          const { order, sub } = item
          const requested = sub.reason === 'requested'
          const cand = sub.options[0]
          const isTheRequest = requested && cand.id === sub.requestedId
          const plainRequest = isTheRequest && sub.modType !== 'swap' // "no x" / "extra x" / "x on the side"
          const rest = sub.options.slice(1)
          return (
            <div key={`${order.id}-${sub.id}`} className="queue-item">
              <div className="row between">
                <b>#{order.id} · {itemLabel(order)} <span className="muted">for {order.name}</span></b>
                <span className={`channel ch-${order.channel.toLowerCase().replace(/[^a-z]/g, '')}`}>{order.channel}</span>
              </div>

              <div className="queue-why">
                <span className={`reason-tag reason-${sub.reason}`}>{REASON_TAG[sub.reason]}</span>{' '}
                {requested ? <>Customer wants <b>{describeMod(subToMod(sub))}</b></> : <><b>{ingName(sub.missing)}</b> - {sub.detail}</>}
                {order.allergens.length > 0 && <span className="allergy-inline"> · ALLERGY: {order.allergens.map((a) => a.name).join(', ')}</span>}
              </div>

              <div className="queue-cand">
                {plainRequest ? (
                  <div>Can the kitchen do this? <b>{describeMod(subToMod(sub))}</b></div>
                ) : (
                  <>
                    <div>
                      {isTheRequest ? 'Requested swap: ' : requested ? 'Suggested replacement: ' : 'Proposed swap: '}<b>{cand.name}</b>
                      {cand.score != null && <span className="score"> score {cand.score}</span>}
                    </div>
                    <div className="why">{cand.reason}</div>
                  </>
                )}
                {sub.warning && <div className="danger">⚠ {sub.warning}</div>}
                {sub.blocked.length > 0 && <div className="why">Auto-blocked: {sub.blocked.map((b) => `${b.name} (${b.reason.toLowerCase()})`).join('; ')}</div>}
                {sub.rejected?.length > 0 && <div className="why">Already ruled out: {sub.rejected.map((r) => r.name).join(', ')}</div>}
              </div>

              <div className="row">
                <button className="btn yes" onClick={() => decide(item, 'possible')}>✓ Possible</button>
                <button className="btn no" onClick={() => decide(item, 'not_possible')}>✕ Not possible</button>
              </div>
              <small className="muted">{notPossibleHint(sub, isTheRequest, rest)}</small>
            </div>
          )
        })}
      </div>
    </section>
  )
}
