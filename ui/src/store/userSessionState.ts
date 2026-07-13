import { queryClient } from "@/api/queryClient"
import { useNavidromeStore } from "@/store/navidromeStore"
import { usePlayerStore } from "@/store/playerStore"
import {
  clearPersistedPlaybackPosition,
  clearPersistedSessionId,
} from "@/store/sessionPersistence"
import { useUIStore } from "@/store/uiStore"

export function resetUserSessionState(): void {
  usePlayerStore.getState().resetForLogout()
  useNavidromeStore.getState().resetForLogout()
  useUIStore.getState().resetForLogout()
  clearPersistedSessionId()
  clearPersistedPlaybackPosition()
  queryClient.clear()
}
