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

export async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
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
