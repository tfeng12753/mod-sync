import { useState } from 'react'
import { StoreProvider } from './StoreContext.jsx'
import { useStore } from './useStore.js'
import { API_URL, USE_MOCK } from './api.js'
import CustomerView from './components/CustomerView.jsx'
import StaffView from './components/StaffView.jsx'
import Icon from './components/Icon.jsx'

function ApiBadge() {
  const { state } = useStore()
  if (USE_MOCK) return <span className="api-badge mock" title="VITE_USE_MOCK=1: in-browser mock, no backend">Mock API</span>
  const label = { ok: 'API connected', down: 'API offline', checking: 'Connecting…' }[state.health]
  return <span className={`api-badge ${state.health}`} title={API_URL}><i aria-hidden="true" />{label}</span>
}

function Shell() {
  const { state } = useStore()
  const [role, setRole] = useState('staff')
  const [section, setSection] = useState('menu')
  // Bumped on every "Your orders" click so CustomerView scrolls even when it is already open.
  const [ordersFocus, setOrdersFocus] = useState(0)

  const mine = state.orders.filter((o) => state.myOrderIds.includes(o.order_id))
  const needsMe = mine.filter((o) => o.status === 'awaiting_customer').length
  const needsStaff = state.orders.filter((o) => o.status === 'awaiting_staff').length

  const goOrders = () => {
    setRole('customer')
    setSection('orders')
    setOrdersFocus((n) => n + 1)
  }
  const goMenu = () => {
    setRole('customer')
    setSection('menu')
    document.querySelector('main.content')?.scrollTo({ top: 0, behavior: 'smooth' })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const nav = [
    { key: 'menu', label: 'Menu', icon: 'menu', active: role === 'customer' && section === 'menu', onClick: goMenu },
    { key: 'orders', label: 'Your orders', icon: 'bag', active: role === 'customer' && section === 'orders', badge: needsMe || null, onClick: goOrders },
    { key: 'kitchen', label: 'Kitchen / staff', icon: 'chef', active: role === 'staff', badge: needsStaff || null, onClick: () => setRole('staff') },
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
            <button key={n.key} className={`nav-item ${n.active ? 'active' : ''}`} aria-current={n.active ? 'page' : undefined} onClick={n.onClick}>
              <Icon name={n.icon} />
              <span>{n.label}</span>
              {n.badge && <span className="nav-badge" aria-label={`${n.badge} need attention`}>{n.badge}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <p className="tag">Modifiers &amp; substitutions, handled.</p>
          <ApiBadge />
        </div>
      </aside>
      <main className="content">{role === 'customer' ? <CustomerView ordersFocus={ordersFocus} /> : <StaffView />}</main>
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
