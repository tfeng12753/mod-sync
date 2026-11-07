// Thin client for the mod-sync backend (see docs/api-contract.md).
// Development uses localhost:8000; production uses the UI's own origin.

const defaultApiUrl = import.meta.env.DEV ? 'http://localhost:8000' : ''
export const API_URL = (import.meta.env.VITE_API_URL ?? defaultApiUrl).replace(/\/+$/, '')
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === '1'

export class ApiError extends Error {
  constructor(status, detail) {
    super(friendlyMessage(status, detail))
    this.name = 'ApiError'
    this.status = status // 0 = network error (backend not reachable)
    this.detail = detail
  }
}

function friendlyMessage(status, detail) {
  if (status === 0) return `Can't reach the kitchen server at ${API_URL}. Is the backend running?`
  if (status === 502) return detail || "We couldn't safely read that request. Please try rephrasing it, or call the restaurant."
  if (status === 404) return detail || 'Not found.'
  if (status === 409) return detail || 'That order can no longer be changed.'
  return detail || `Request failed (${status}).`
}

function detailText(body) {
  if (!body || body.detail == null) return ''
  if (typeof body.detail === 'string') return body.detail
  // FastAPI validation errors: [{loc, msg, type}]
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
  return JSON.stringify(body.detail)
}

let mockModule = null
async function mock() {
  // Only bundled/loaded when VITE_USE_MOCK=1 (the condition is replaced at build time).
  if (!mockModule) mockModule = await import('./mockApi.js')
  return mockModule
}

async function request(method, path, body) {
  if (USE_MOCK) {
    const { handle } = await mock()
    const res = await handle(method, path, body)
    if (res.status >= 400) throw new ApiError(res.status, detailText(res.body))
    return res.body
  }

  let res
  try {
    res = await fetch(`${API_URL}${path}`, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, '')
  }

  let data = null
  const text = await res.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = { detail: text }
    }
  }
  if (!res.ok) throw new ApiError(res.status, detailText(data))
  return data
}

const enc = encodeURIComponent

export const api = {
  health: () => request('GET', '/api/health'),
  menu: () => request('GET', '/api/menu'),
  evaluate: ({ dishId, qty = 1, text = '', clarifications, channel, customerName }) => {
    const body = { dish_id: dishId, qty, text }
    if (clarifications?.length) body.clarifications = clarifications
    if (channel) body.channel = channel
    if (customerName) body.customer_name = customerName
    return request('POST', '/api/evaluate', body)
  },
  accept: (orderId, optionId = null) => request('POST', `/api/orders/${enc(orderId)}/accept`, { option_id: optionId }),
  approve: (orderId, optionId = null) => request('POST', `/api/orders/${enc(orderId)}/review`, { action: 'approve', option_id: optionId }),
  edit: (orderId, optionId) => request('POST', `/api/orders/${enc(orderId)}/review`, { action: 'edit', option_id: optionId }),
  reject: (orderId, note = '') => request('POST', `/api/orders/${enc(orderId)}/review`, { action: 'reject', note }),
  cancel: (orderId) => request('POST', `/api/orders/${enc(orderId)}/cancel`),
  orders: () => request('GET', '/api/orders'),
  order: (orderId) => request('GET', `/api/orders/${enc(orderId)}`),
  stock: () => request('GET', '/api/stock'),
  setStock: (itemId, onHand, reason = '') => request('POST', `/api/stock/${enc(itemId)}`, { on_hand: onHand, reason }),
  resetDemo: () => request('POST', '/api/demo/reset'),
}
