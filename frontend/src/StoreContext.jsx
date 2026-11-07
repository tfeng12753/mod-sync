import { useCallback, useEffect, useMemo, useReducer } from 'react'
import { reducer, initialState } from './store.js'
import { api } from './api.js'
import { StoreContext } from './useStore.js'

const ORDERS_POLL_MS = 2000
const STOCK_POLL_MS = 5000

export function StoreProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, undefined, initialState)

  const loadMenu = useCallback(async () => {
    try {
      dispatch({ type: 'MENU', menu: await api.menu() })
    } catch (e) {
      dispatch({ type: 'MENU', error: e.message })
    }
  }, [])

  const refreshOrders = useCallback(async () => {
    try {
      dispatch({ type: 'ORDERS', orders: await api.orders() })
    } catch (e) {
      dispatch({ type: 'ORDERS', error: e.message })
    }
  }, [])

  const refreshStock = useCallback(async () => {
    try {
      const res = await api.stock()
      dispatch({ type: 'STOCK', items: res.items })
    } catch (e) {
      dispatch({ type: 'STOCK', error: e.message })
    }
  }, [])

  const checkHealth = useCallback(async () => {
    try {
      const res = await api.health()
      dispatch({ type: 'HEALTH', health: res?.status === 'ok' ? 'ok' : 'down' })
    } catch {
      dispatch({ type: 'HEALTH', health: 'down' })
    }
  }, [])

  // Initial load + polling.
  useEffect(() => {
    checkHealth()
    loadMenu()
    refreshOrders()
    refreshStock()
    const tOrders = setInterval(refreshOrders, ORDERS_POLL_MS)
    const tStock = setInterval(() => {
      refreshStock()
      checkHealth()
    }, STOCK_POLL_MS)
    const tClock = setInterval(() => dispatch({ type: 'TICK', now: Date.now() }), 1000)
    return () => {
      clearInterval(tOrders)
      clearInterval(tStock)
      clearInterval(tClock)
    }
  }, [checkHealth, loadMenu, refreshOrders, refreshStock])

  // Retry the menu once the backend comes back.
  useEffect(() => {
    if (state.health === 'ok' && state.menu.length === 0) loadMenu()
  }, [state.health, state.menu.length, loadMenu])

  const value = useMemo(
    () => ({ state, dispatch, refreshOrders, refreshStock, loadMenu }),
    [state, refreshOrders, refreshStock, loadMenu],
  )
  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>
}
