import { useStore } from '../useStore.js'
import TicketCard from './TicketCard.jsx'
import DemoControls from './DemoControls.jsx'

export default function StaffView() {
  const { state, dispatch } = useStore()
  const { orders, menu, now, packed, handedOff, ordersError } = state
  const dishOf = (o) => menu.find((d) => d.id === o.dish?.id)

  const queue = orders.filter((o) => o.status === 'awaiting_staff')
  const waiting = orders.filter((o) => o.status === 'awaiting_customer')
  const kitchen = orders.filter((o) => o.status === 'confirmed' && !handedOff[o.order_id])
  const done = orders.filter((o) => o.status === 'confirmed' && handedOff[o.order_id])
  const closed = orders.filter((o) => o.status === 'rejected' || o.status === 'expired')

  const card = (o) => (
    <TicketCard key={o.order_id} order={o} dish={dishOf(o)} now={now} dispatch={dispatch}
      packed={packed[o.order_id]} handedOff={!!handedOff[o.order_id]} />
  )

  return (
    <div className="layout">
      <section>
        <h1 className="page-title">Kitchen</h1>
        {ordersError && <div className="error">{ordersError}</div>}

        <h2>Needs staff review <span className="count">{queue.length} waiting</span></h2>
        {queue.length === 0 && <p className="empty">No requests waiting for review. Allergy and dietary requests land here.</p>}
        <div className="grid">{queue.map(card)}</div>

        <h2 className="section">Kitchen board <span className="count">{kitchen.length} confirmed</span></h2>
        {kitchen.length === 0 && <p className="empty">No confirmed orders. Use the demo controls to send one, or place one from the Customer app.</p>}
        <div className="grid">{kitchen.map(card)}</div>

        {waiting.length > 0 && (
          <>
            <h3 className="muted section">Waiting on customer</h3>
            <div className="grid">{waiting.map(card)}</div>
          </>
        )}
        {done.length > 0 && (
          <>
            <h3 className="muted section">Handed off</h3>
            <div className="grid">{done.map(card)}</div>
          </>
        )}
        {closed.length > 0 && (
          <>
            <h3 className="muted section">Rejected / expired</h3>
            <div className="grid">{closed.map(card)}</div>
          </>
        )}
      </section>
      <DemoControls />
    </div>
  )
}
