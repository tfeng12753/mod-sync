import { createContext, useContext } from 'react'

// Kept separate from StoreContext.jsx so that file only exports a component (fast refresh).
export const StoreContext = createContext(null)

export function useStore() {
  return useContext(StoreContext)
}
