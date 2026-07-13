import { Outlet, Navigate, useLocation } from "react-router"
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { getSession, type SessionState } from "@/api/auth"
import { isUnauthorized } from "@/lib/authRedirect"

export type SessionGateState = "app" | "login" | "offline"

export function sessionGateState(
  data: SessionState | undefined,
  error: unknown,
  isError: boolean
): SessionGateState {
  // A previously known good session remains trustworthy when a later focus
  // refetch cannot reach the server. Only an explicit server verdict logs out.
  if (data) return data.authenticated ? "app" : "login"
  if (isUnauthorized(error)) return "login"
  return isError ? "offline" : "app"
}

// Initial-load gate: redirect to /login only when the server explicitly said
// the session is absent/expired (authenticated=false or a 401). A transient
// network failure — mobile radio not yet up after the tab resumes, server
// restarting — is NOT a logout: the session cookie is usually still valid,
// so we keep the app (or show a retry screen) instead of bouncing to /login.
export default function RequireAuth() {
  const { t } = useTranslation("auth")
  const location = useLocation()
  const { data, error, isPending, isError, refetch } = useQuery({
    queryKey: ["auth", "session"],
    queryFn: getSession,
    retry: (failureCount, err) => !isUnauthorized(err) && failureCount < 2,
    staleTime: 60_000,
  })

  if (isPending) {
    return <div className="h-svh w-full bg-background" />
  }

  const gate = sessionGateState(data, error, isError)
  if (gate === "login") {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  if (gate === "offline") {
    return (
      <div className="flex h-svh w-full flex-col items-center justify-center gap-4 bg-background">
        <p className="text-sm text-muted-foreground">{t("offlineMessage")}</p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="rounded-2xl border border-border/50 px-4 py-2 text-sm text-foreground hover:border-ring"
        >
          {t("actions.retry", { ns: "common" })}
        </button>
      </div>
    )
  }

  return <Outlet />
}
