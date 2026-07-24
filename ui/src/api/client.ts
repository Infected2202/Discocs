export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

// CapacitorHttp (native Android/iOS builds, see ui/capacitor.config.ts) only
// patches window.fetch/XMLHttpRequest for absolute URLs — a root-relative
// path like "/api/v1/auth/session" falls straight back through the WebView's
// own fetch, which the local WebViewAssetLoader intercepts and answers with
// the bundled index.html (SPA fallback) instead of reaching the real network.
// Resolving against location.origin here is a no-op on the web (already the
// same origin) and fixes native without touching every call site.
function resolveApiUrl(url: string): string {
  return new URL(url, globalThis.location.origin).toString()
}

export async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(resolveApiUrl(url), {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  })

  if (!res.ok) {
    let code = "api_error"
    let message = `HTTP ${res.status}`
    try {
      const body = await res.json()
      code = body?.error?.code ?? code
      message = body?.error?.message ?? body?.detail ?? message
    } catch {
      // ignore parse failure
    }
    throw new ApiError(res.status, code, message)
  }

  return res.json() as Promise<T>
}

export function apiUrl(path: string, params?: Record<string, string | number | boolean | undefined>): string {
  const url = new URL(path, globalThis.location.origin)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) url.searchParams.set(key, String(value))
    }
  }
  return url.pathname + url.search
}
