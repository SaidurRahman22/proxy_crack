import { env } from '@/env'

import { NetworkError, toApiError } from './errors'
import { tokenStore } from './tokenStore'
import { buildQueryString, type RequestSpec, type Transport } from './transport'

function parseBody(text: string, contentType: string | null): unknown {
  if (text === '') return null
  if (contentType && contentType.includes('application/json')) {
    try {
      return JSON.parse(text) as unknown
    } catch {
      return text
    }
  }
  return text
}

/** `Retry-After` is seconds or an HTTP date; both are turned into seconds. */
function retryAfter(raw: string | null): number | null {
  if (!raw) return null
  const seconds = Number(raw)
  if (Number.isFinite(seconds)) return seconds
  const when = Date.parse(raw)
  if (Number.isNaN(when)) return null
  return Math.max(0, (when - Date.now()) / 1000)
}

function authHeader(spec: RequestSpec): Record<string, string> {
  if (spec.auth === false) return {}
  const token = tokenStore.getAccess()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/**
 * `fetch` exposes download progress but not upload progress, and the CSV
 * screen needs a real progress bar rather than an indeterminate spinner. XHR
 * is the only browser API that reports bytes sent, so multipart requests that
 * ask for progress take this path; everything else uses `fetch`.
 */
function xhrRequest<T>(url: string, spec: RequestSpec, formData: FormData): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open(spec.method, url, true)
    xhr.responseType = 'text'

    for (const [key, value] of Object.entries(authHeader(spec))) {
      xhr.setRequestHeader(key, value)
    }
    xhr.setRequestHeader('Accept', 'application/json')

    if (spec.onProgress) {
      const report = spec.onProgress
      xhr.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable && event.total > 0) report(event.loaded / event.total)
      })
    }

    xhr.addEventListener('load', () => {
      const body = parseBody(xhr.responseText, xhr.getResponseHeader('Content-Type'))
      if (xhr.status >= 200 && xhr.status < 300) {
        spec.onProgress?.(1)
        resolve((body ?? null) as T)
      } else {
        reject(toApiError(xhr.status, body, retryAfter(xhr.getResponseHeader('Retry-After'))))
      }
    })
    xhr.addEventListener('error', () => reject(new NetworkError('Could not reach the API.')))
    xhr.addEventListener('timeout', () => reject(new NetworkError('The request timed out.')))
    xhr.addEventListener('abort', () => reject(new NetworkError('The upload was cancelled.')))

    if (spec.signal) {
      if (spec.signal.aborted) {
        xhr.abort()
        return
      }
      spec.signal.addEventListener('abort', () => xhr.abort(), { once: true })
    }

    xhr.send(formData)
  })
}

/** Talks to the real Django admin API. */
export function createHttpTransport(baseUrl: string = env.apiBaseUrl): Transport {
  return {
    async request<T>(spec: RequestSpec): Promise<T> {
      const url = `${baseUrl}${spec.path}${buildQueryString(spec.query)}`

      if (spec.formData) return xhrRequest<T>(url, spec, spec.formData)

      const headers: Record<string, string> = {
        Accept: 'application/json',
        ...authHeader(spec),
      }
      const init: RequestInit = { method: spec.method, headers }
      if (spec.signal) init.signal = spec.signal
      if (spec.body !== undefined) {
        headers['Content-Type'] = 'application/json'
        init.body = JSON.stringify(spec.body)
      }

      let response: Response
      try {
        response = await fetch(url, init)
      } catch (cause) {
        if (spec.signal?.aborted) throw new NetworkError('The request was cancelled.', cause)
        throw new NetworkError(
          `Could not reach the API at ${baseUrl}. Check that the backend is running.`,
          cause,
        )
      }

      // 204 on logout / set_password / delete.
      if (response.status === 204) return null as T

      const text = await response.text()
      const body = parseBody(text, response.headers.get('Content-Type'))

      if (!response.ok) {
        throw toApiError(response.status, body, retryAfter(response.headers.get('Retry-After')))
      }
      return (body ?? null) as T
    },
  }
}
