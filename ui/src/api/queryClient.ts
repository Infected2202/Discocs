import { QueryClient, QueryCache, MutationCache } from "@tanstack/react-query"
import { isUnauthorized, redirectToLogin } from "@/lib/authRedirect"
import {
  clearPersistedPlaybackPosition,
  clearPersistedSessionId,
} from "@/store/sessionPersistence"

function handleGlobalError(error: unknown): void {
  if (!isUnauthorized(error)) return
  clearPersistedSessionId()
  clearPersistedPlaybackPosition()
  redirectToLogin()
}


// A 401 from any query/mutation means the session expired mid-session — bounce
// to the login page. Initial-load gating is handled by RequireAuth.
export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: handleGlobalError,
  }),
  mutationCache: new MutationCache({
    onError: handleGlobalError,
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
})
