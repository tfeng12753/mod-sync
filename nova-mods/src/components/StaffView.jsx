import { useStore } from '../StoreContext.jsx'
import TicketCard from './TicketCard.jsx'
import DemoControls from './DemoControls.jsx'
import SubstitutionQueue from './SubstitutionQueue.jsx'

export default function StaffView() {
  const { state, dispatch } = useStore()
  const active = state.orders.filter((o) => o.status !== 'handed_off' && o.status !== 'removed')
  const removed = state.orders.filter((o) => o.status === 'removed')
  const done = state.orders.filter((o) => o.status === 'handed_off')

  return (
    <div className="layout">
      <section>
        <SubstitutionQueue orders={state.orders} settings={state.settings} dispatch={dispatch} />
        <h2>Kitchen board <span className="count">{active.length} open</span></h2>
        {active.length === 0 && <p className="empty">No open orders. Use the demo controls to send one, or place one from the Customer app.</p>}
        <div className="grid">
          {active.map((o) => <TicketCard key={o.id} order={o} now={state.now} dispatch={dispatch} />)}
        </div>
        {removed.length > 0 && (
          <>
            <h3 className="muted">Removed from order (refunded)</h3>
            <div className="grid">{removed.map((o) => <TicketCard key={o.id} order={o} now={state.now} dispatch={dispatch} />)}</div>
          </>
        )}
        {done.length > 0 && (
          <>
            <h3 className="muted">Handed off</h3>
            <div className="grid">{done.map((o) => <TicketCard key={o.id} order={o} now={state.now} dispatch={dispatch} />)}</div>
          </>
        )}
      </section>
      <DemoControls state={state} dispatch={dispatch} />
    </div>
  )
}
