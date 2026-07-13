import { apiFetch, apiUrl } from "./client"
import type { SupportedLanguage } from "@/i18n"

export interface UserSettings {
  language: SupportedLanguage
}

export async function getUserSettings(): Promise<UserSettings> {
  return apiFetch<UserSettings>(apiUrl("/api/v1/me/settings"))
}

export async function updateUserSettings(
  patch: Partial<UserSettings>
): Promise<UserSettings> {
  return apiFetch<UserSettings>(apiUrl("/api/v1/me/settings"), {
    method: "PATCH",
    body: JSON.stringify(patch),
  })
}
