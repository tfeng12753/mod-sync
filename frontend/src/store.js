// Client-only UI state. Everything about orders, options and stock comes from the API;
// this only keeps what the backend doesn't track: data snapshots, pack-check ticks,
// "handed off" marks, the orders placed from this browser, and an activity log.

export function initialState() {
  return {
    menu: [],
    menuError: null,
    orders: [],
    ordersError: null,
    stock: [],
    stockError: null,
    health: 'checking', // checking | ok | down
    now: Date.now(),
    myOrderIds: [],
    packed: {}, // { [orderId]: { [checkKey]: true } }
    handedOff: {}, // { [orderId]: true }
    log: [],
  }
}

function addLog(log, text) {
  return [{ t: Date.now(), text }, ...log].slice(0, 30)
}

export function reducer(state, action) {
  switch (action.type) {
    case 'MENU':
      return { ...state, menu: action.menu ?? state.menu, menuError: action.error ?? null }
    case 'ORDERS':
      return { ...state, orders: action.orders ?? state.orders, ordersError: action.error ?? null }
    case 'ORDER': {
      // Upsert one order returned by accept/review/get.
      const exists = state.orders.some((o) => o.order_id === action.order.order_id)
      const orders = exists
        ? state.orders.map((o) => (o.order_id === action.order.order_id ? action.order : o))
        : [action.order, ...state.orders]
      return { ...state, orders }
    }
    case 'STOCK':
      return { ...state, stock: action.items ?? state.stock, stockError: action.error ?? null }
    case 'STOCK_ITEM':
      return { ...state, stock: state.stock.map((s) => (s.id === action.item.id ? action.item : s)) }
    case 'HEALTH':
      return state.health === action.health ? state : { ...state, health: action.health }
    case 'TICK':
      return { ...state, now: action.now }
    case 'MINE':
      return state.myOrderIds.includes(action.orderId) ? state : { ...state, myOrderIds: [action.orderId, ...state.myOrderIds] }
    case 'TOGGLE_PACK': {
      const cur = state.packed[action.orderId] ?? {}
      return { ...state, packed: { ...state.packed, [action.orderId]: { ...cur, [action.key]: !cur[action.key] } } }
    }
    case 'HAND_OFF':
      return { ...state, handedOff: { ...state.handedOff, [action.orderId]: true }, log: addLog(state.log, `${action.orderId} handed off`) }
    case 'LOG':
      return { ...state, log: addLog(state.log, action.text) }
    case 'RESET_CLIENT':
      return { ...state, orders: [], myOrderIds: [], packed: {}, handedOff: {}, log: addLog([], 'Demo reset') }
    default:
      return state
  }
}
