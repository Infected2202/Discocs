import { Outlet, Navigate, useLocation } from "react-router"
import { useQuery } from "@tanstack/react-query"
import { getSession } from "@/api/auth"
import { isUnauthorized } from "@/lib/authRedirect"

// Initial-load gate: redirect to /login only when the server explicitly said
// the session is absent/expired (authenticated=false or a 401). A transient
// network failure — mobile radio not yet up after the tab resumes, server
// restarting — is NOT a logout: the session cookie is usually still valid,
// so we keep the app (or show a retry screen) instead of bouncing to /login.
export default function RequireAuth() {
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

  // The server answered: trust its verdict (data survives a later failed
  // focus-refetch, so a network error never overrides a known session).
  if (data ? !data.authenticated : isUnauthorized(error)) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  // No session state at all and the request failed — can't tell whether the
  // session is valid; offer a retry instead of a false logout.
  if (isError && data === undefined) {
    return (
      <div className="flex h-svh w-full flex-col items-center justify-center gap-4 bg-background">
        <p className="text-sm text-muted-foreground">Нет соединения с сервером</p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="rounded-2xl border border-border/50 px-4 py-2 text-sm text-foreground hover:border-ring"
        >
          Повторить
        </button>
      </div>
    )
  }

  return <Outlet />
}
