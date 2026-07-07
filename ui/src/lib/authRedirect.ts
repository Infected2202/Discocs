import { ApiError } from "@/api/client"

export const LOGIN_PATH = "/login"

/** Hard-redirect to the login page unless we are already there. */
export function redirectToLogin(): void {
  if (globalThis.location.pathname !== LOGIN_PATH) {
    globalThis.location.assign(LOGIN_PATH)
  }
}

/** True for an expired/absent session (401) — the signal to re-login. */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}
