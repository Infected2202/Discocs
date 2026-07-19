import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "./client"

export type ShareSourceType = "track" | "release"

export interface ShareCapabilities {
  enabled: boolean
  can_create: boolean
}

export interface ManagedShare {
  id: string
  source_type: ShareSourceType
  source_id: number
  source_label: string
  title: string | null
  item_count: number
  created_at: string
  expires_at: string | null
  revoked_at: string | null
  last_accessed_at: string | null
  access_count: number
  token_prefix: string
  status: "active" | "expired" | "revoked"
}

export interface PublicShareItem {
  position: number
  title: string
  artist: string | null
  duration: number | null
  available: boolean
  audio_url: string
}

export interface PublicShare {
  kind: ShareSourceType
  title: string
  subtitle: string | null
  expires_at: string | null
  artwork_url: string
  items: PublicShareItem[]
}

export function useShareCapabilities() {
  return useQuery<ShareCapabilities>({
    queryKey: ["share-capabilities"],
    queryFn: () => apiFetch("/api/v1/shares/capabilities"),
    staleTime: 60_000,
    retry: false,
  })
}

export function createShare(input: {
  source_type: ShareSourceType
  source_id: number
  title?: string
  expires_at?: string | null
}): Promise<{ share: ManagedShare; url: string }> {
  return apiFetch("/api/v1/shares", { method: "POST", body: JSON.stringify(input) })
}

export function listShares(): Promise<{ items: ManagedShare[] }> {
  return apiFetch("/api/v1/shares")
}

export function revokeShare(id: string): Promise<void> {
  return fetch(`/api/v1/shares/${id}`, {
    method: "DELETE",
    credentials: "same-origin",
  }).then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
  })
}

export function fetchPublicShare(token: string): Promise<PublicShare> {
  return apiFetch(`/api/v1/public/shares/${encodeURIComponent(token)}`)
}
