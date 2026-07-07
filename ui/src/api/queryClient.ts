import { QueryClient, QueryCache, MutationCache } from "@tanstack/react-query"
import { isUnauthorized, redirectToLogin } from "@/lib/authRedirect"

// A 401 from any query/mutation means the session expired mid-session — bounce
// to the login page. Initial-load gating is handled by RequireAuth.
export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error) => {
      if (isUnauthorized(error)) redirectToLogin()
    },
  }),
  mutationCache: new MutationCache({
    onError: (error) => {
      if (isUnauthorized(error)) redirectToLogin()
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
})
