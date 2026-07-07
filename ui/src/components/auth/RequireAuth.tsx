import { Outlet, Navigate } from "react-router"
import { useQuery } from "@tanstack/react-query"
import { getSession } from "@/api/auth"

// Initial-load gate: if the session is absent/expired, redirect to /login.
// Mid-session 401s are handled globally in queryClient (redirectToLogin).
export default function RequireAuth() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["auth", "session"],
    queryFn: getSession,
    retry: false,
    staleTime: 60_000,
  })

  if (isPending) {
    return <div className="h-svh w-full bg-background" />
  }

  if (isError || !data?.authenticated) {
    return <Navigate to="/login" replace />
  }

  return <Outlet />
}
