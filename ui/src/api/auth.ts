import { apiFetch } from "./client"
import { resetUserSessionState } from "@/store/userSessionState"

export interface SessionState {
  authenticated: boolean
  username: string | null
  enabled: boolean
}

export async function getSession(): Promise<SessionState> {
  return apiFetch<SessionState>("/api/v1/auth/session")
}

export async function login(username: string, password: string): Promise<{ authenticated: boolean; username: string }> {
  return apiFetch("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  })
}

export async function logout(): Promise<void> {
  try {
    await apiFetch("/api/v1/auth/logout", { method: "POST" })
  } finally {
    resetUserSessionState()
  }
}
