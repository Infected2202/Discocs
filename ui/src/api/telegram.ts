import { useQuery } from "@tanstack/react-query"
import { apiFetch } from "./client"
import type { ShareSourceType } from "./shares"

export interface TelegramLinkStatus {
  enabled: boolean
  linked: boolean
  link: { telegram_username: string | null; linked_at: string } | null
}

export const TELEGRAM_LINK_QUERY_KEY = ["telegram-link"] as const

export function useTelegramLink() {
  return useQuery<TelegramLinkStatus>({
    queryKey: TELEGRAM_LINK_QUERY_KEY,
    queryFn: () => apiFetch("/api/v1/telegram/link"),
    staleTime: 60_000,
    retry: false,
  })
}

/** Mint a one-shot deep link; the bot redeems it when the user presses Start. */
export function startTelegramLink(): Promise<{ url: string; expires_at: string }> {
  return apiFetch("/api/v1/telegram/link", { method: "POST" })
}

export async function unlinkTelegram(): Promise<void> {
  const response = await fetch(new URL("/api/v1/telegram/link", globalThis.location.origin), {
    method: "DELETE",
    credentials: "same-origin",
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
}

export function sendToTelegram(input: {
  source_type: ShareSourceType
  source_id: number
}): Promise<{ status: string }> {
  return apiFetch("/api/v1/telegram/send", { method: "POST", body: JSON.stringify(input) })
}
