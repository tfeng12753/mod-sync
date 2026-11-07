import { useState } from 'react'
import { StoreProvider } from './StoreContext.jsx'
import { useStore } from './useStore.js'
import CustomerView from './components/CustomerView.jsx'
import StaffView from './components/StaffView.jsx'
import Icon from './components/Icon.jsx'

function Shell() {
  const { state } = useStore()
  const [activeView, setActiveView] = useState('kitchen')
  // Bumped on every "Your orders" click so CustomerView scrolls even when it is already open.
  const [ordersFocus, setOrdersFocus] = useState(0)

  const mine = state.orders.filter((o) => state.myOrderIds.includes(o.order_id))
  const needsMe = mine.filter((o) => o.status === 'awaiting_customer').length
  const needsStaff = state.orders.filter((o) => o.status === 'awaiting_staff').length

  const goOrders = () => {
    setActiveView('orders')
    setOrdersFocus((n) => n + 1)
  }
  const goMenu = () => {
    setActiveView('menu')
    document.querySelector('main.content')?.scrollTo({ top: 0, behavior: 'smooth' })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const nav = [
    { key: 'menu', label: 'Menu', icon: 'menu', onClick: goMenu },
    { key: 'orders', label: 'Your orders', icon: 'bag', badge: needsMe || null, onClick: goOrders },
    { key: 'kitchen', label: 'Kitchen / staff', icon: 'chef', badge: needsStaff || null, onClick: () => setActiveView('kitchen') },
  ]

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true"><Icon name="bowl" /></span>
          <span className="logo">ModSync</span>
        </div>
        <nav className="nav" aria-label="Views">
          {nav.map((n) => (
            <button key={n.key} className={`nav-item ${activeView === n.key ? 'active' : ''}`} aria-current={activeView === n.key ? 'page' : undefined} onClick={n.onClick}>
              <Icon name={n.icon} />
              <span>{n.label}</span>
              {n.badge && <span className="nav-badge" aria-label={`${n.badge} need attention`}>{n.badge}</span>}
            </button>
          ))}
        </nav>
      </aside>
      <main className="content">{activeView === 'kitchen' ? <StaffView /> : <CustomerView ordersFocus={ordersFocus} />}</main>
    </div>
  )
}

export default function App() {
  return (
    <StoreProvider>
      <Shell />
    </StoreProvider>
  )
}
