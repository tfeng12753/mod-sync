import { useState } from 'react'
import { StoreProvider } from './StoreContext.jsx'
import { useStore } from './useStore.js'
import { API_URL, USE_MOCK } from './api.js'
import CustomerView from './components/CustomerView.jsx'
import StaffView from './components/StaffView.jsx'

function ApiBadge() {
  const { state } = useStore()
  if (USE_MOCK) return <span className="api-badge mock" title="VITE_USE_MOCK=1: in-browser mock, no backend">Mock API</span>
  const label = { ok: 'API connected', down: 'API offline', checking: 'Connecting…' }[state.health]
  return <span className={`api-badge ${state.health}`} title={API_URL}>{label}</span>
}

export default function App() {
  const [role, setRole] = useState('staff')

  return (
    <StoreProvider>
      <header className="topbar">
        <div className="brand">
          <span className="logo">Mod<b>Sync</b></span>
          <span className="tag">modifiers &amp; substitutions, handled</span>
          <ApiBadge />
        </div>
        <div className="tabs" role="tablist">
          <button className={role === 'customer' ? 'tab active' : 'tab'} onClick={() => setRole('customer')}>Customer app</button>
          <button className={role === 'staff' ? 'tab active' : 'tab'} onClick={() => setRole('staff')}>Kitchen / staff</button>
        </div>
      </header>
      <main>{role === 'customer' ? <CustomerView /> : <StaffView />}</main>
    </StoreProvider>
  )
}
