import { ApiError } from "@/api/client"

// A dropped VPN tunnel (or any connectivity failure) never reaches the
// server, so `apiFetch` rejects with a plain `TypeError`/`Error` from
// `fetch()` itself rather than an `ApiError`. Only that case should be
// retried forever in the background — a real HTTP error (404/401/500/...)
// came back as an `ApiError` and means the server answered, so retrying it
// indefinitely would just hammer a legitimately failing/missing resource.
export function isNetworkError(error: unknown): boolean {
  return error != null && !(error instanceof ApiError)
}
