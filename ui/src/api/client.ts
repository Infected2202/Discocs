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
  const resolvedUrl = resolveApiUrl(url)
  const res = await fetch(resolvedUrl, {
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

  // A 200 with an unparseable body has repeatedly turned out to be a wrong
  // network layer answering instead of the API (e.g. a local WebView asset
  // server returning the app's own index.html for an unmatched path) — bare
  // "Unexpected token '<'" gives no way to tell which without device logs.
  // Deliberately NOT reading the body a second time (clone()+text()) for a
  // preview: a Response body can only be read once, clone() must happen
  // before the first read to have any value, and doing that unconditionally
  // broke every existing test that mocks fetch with a plain {ok, json}
  // object (no clone()) — status + content-type alone already answers the
  // question that mattered here (e.g. content-type "text/html" means
  // something other than the API answered), without that fragility.
  try {
    return (await res.json()) as T
  } catch (parseError) {
    const contentType = res.headers?.get?.("content-type") ?? "unknown"
    throw new Error(
      `Non-JSON response from ${resolvedUrl} (status ${res.status}, content-type "${contentType}")`,
      { cause: parseError }
    )
  }
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
