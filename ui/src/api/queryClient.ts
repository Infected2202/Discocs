import { QueryClient, QueryCache, MutationCache } from "@tanstack/react-query"
import { isUnauthorized, redirectToLogin } from "@/lib/authRedirect"
import { isNetworkError } from "@/lib/apiErrorKind"
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

// A VPN-tunnel drop is a routing-layer failure — the underlying network
// interface usually stays associated, so the browser's online/offline
// events (and React Query's default refetchOnReconnect, which depends on
// them) may never fire. Poll on a plain timer instead, independent of those
// events, but only for genuine connectivity failures: a real HTTP error
// (404/401/500/...) came back from the server and should not retry forever.
const NETWORK_RETRY_INTERVAL_MS = 15_000

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
      refetchOnReconnect: "always",
      refetchInterval: (query) => {
        const state = query.state
        if (state.status === "error" && isNetworkError(state.error)) {
          return NETWORK_RETRY_INTERVAL_MS
        }
        return false
      },
    },
  },
})
