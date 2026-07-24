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
  // "Unexpected token '<'" gives no way to tell which without device logs, so
  // fold in exactly what would otherwise need adb/logcat to see.
  // The clone MUST happen before res.json() is ever attempted: a Response
  // body can only be read once, and clone() throws ("body is already used")
  // once the original has started being consumed — even if that read failed.
  const diagnosticsCopy = res.clone()
  try {
    return (await res.json()) as T
  } catch (parseError) {
    const contentType = res.headers?.get?.("content-type") ?? "unknown"
    const bodyPreview = await diagnosticsCopy.text().then(
      (t) => t.slice(0, 200),
      () => "<unreadable>"
    )
    throw new Error(
      `Non-JSON response from ${resolvedUrl} (status ${res.status}, content-type "${contentType}"): ${bodyPreview}`,
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
