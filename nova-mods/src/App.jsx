import { useState } from 'react'
import { StoreProvider } from './StoreContext.jsx'
import CustomerView from './components/CustomerView.jsx'
import StaffView from './components/StaffView.jsx'

export default function App() {
  const [role, setRole] = useState('staff')

  return (
    <StoreProvider>
      <header className="topbar">
        <div className="brand">
          <span className="logo">Mod<b>Sync</b></span>
          <span className="tag">modifiers &amp; substitutions, handled</span>
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
