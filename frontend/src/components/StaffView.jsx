import { useStore } from '../StoreContext.jsx'
import TicketCard from './TicketCard.jsx'
import DemoControls from './DemoControls.jsx'

export default function StaffView() {
  const { state, dispatch } = useStore()
  const active = state.orders.filter((o) => o.status !== 'handed_off')
  const done = state.orders.filter((o) => o.status === 'handed_off')

  return (
    <div className="layout">
      <section>
        <h2>Kitchen board <span className="count">{active.length} open</span></h2>
        {active.length === 0 && <p className="empty">No open orders. Use the demo controls to send one, or place one from the Customer app.</p>}
        <div className="grid">
          {active.map((o) => <TicketCard key={o.id} order={o} now={state.now} dispatch={dispatch} />)}
        </div>
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
