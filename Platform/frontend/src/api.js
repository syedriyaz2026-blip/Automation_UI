// Use the same-origin Vite proxy first. This works in the in-app browser
// without cross-port restrictions; 8792 remains a local-only fallback.
const API_BASE = import.meta.env.VITE_API_URL || ''
const REQUEST_TIMEOUT_MS = 30_000
const LOCAL_API_FALLBACK = 'http://127.0.0.1:8792'

export async function api(path, options = {}) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  let response
  let requestUrl = API_BASE + path
  try {
    response = await fetch(requestUrl, {
      credentials: 'include',
      ...options,
      signal: options.signal || controller.signal,
      headers: {
        ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        ...(options.headers || {}),
      },
    })
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out. Check that the local API is running.')
    // If the local proxy is unavailable, retry the dedicated local API.
    if (!API_BASE && typeof window !== 'undefined' && ['127.0.0.1', 'localhost'].includes(window.location.hostname)) {
      requestUrl = LOCAL_API_FALLBACK + path
      try {
        response = await fetch(requestUrl, {
          credentials: 'include',
          ...options,
          signal: options.signal || controller.signal,
          headers: {
            ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
            ...(options.headers || {}),
          },
        })
      } catch (fallbackError) {
        if (fallbackError.name === 'AbortError') throw new Error('The request timed out. Check that the local API is running.')
        throw new Error('Cannot reach the local API at http://127.0.0.1:8792. Start the app again and refresh this page.')
      }
    } else {
      throw new Error('Cannot reach the local API. Start the app again and refresh this page.')
    }
  } finally {
    clearTimeout(timeout)
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = typeof data.error === 'string' ? data.error : data.error?.message
    const error = new Error(detail || ('Request failed (' + response.status + ')'))
    error.details = data.error?.details || []
    throw error
  }
  return data
}

export const json = body => ({ method: 'POST', body: JSON.stringify(body) })
