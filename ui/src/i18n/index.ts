import i18n from "i18next"
import { initReactI18next } from "react-i18next"
import LanguageDetector from "i18next-browser-languagedetector"

import enCommon from "./locales/en/common.json"
import enNav from "./locales/en/nav.json"
import enProfile from "./locales/en/profile.json"
import enAuth from "./locales/en/auth.json"
import enSettings from "./locales/en/settings.json"
import enPlayer from "./locales/en/player.json"
import enDashboard from "./locales/en/dashboard.json"
import enMedia from "./locales/en/media.json"
import enSearch from "./locales/en/search.json"
import enArtist from "./locales/en/artist.json"
import enRelease from "./locales/en/release.json"
import enMix from "./locales/en/mix.json"
import enPlaylist from "./locales/en/playlist.json"
import enShare from "./locales/en/share.json"

import ruCommon from "./locales/ru/common.json"
import ruNav from "./locales/ru/nav.json"
import ruProfile from "./locales/ru/profile.json"
import ruAuth from "./locales/ru/auth.json"
import ruSettings from "./locales/ru/settings.json"
import ruPlayer from "./locales/ru/player.json"
import ruDashboard from "./locales/ru/dashboard.json"
import ruMedia from "./locales/ru/media.json"
import ruSearch from "./locales/ru/search.json"
import ruArtist from "./locales/ru/artist.json"
import ruRelease from "./locales/ru/release.json"
import ruMix from "./locales/ru/mix.json"
import ruPlaylist from "./locales/ru/playlist.json"
import ruShare from "./locales/ru/share.json"

export const SUPPORTED_LANGUAGES = ["en", "ru"] as const
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number]

export const LANGUAGE_NAMES: Record<SupportedLanguage, string> = {
  en: "English",
  ru: "Русский",
}

export const LANGUAGE_STORAGE_KEY = "discocs-language"

export const NAMESPACES = [
  "common", "nav", "profile", "auth", "settings", "player",
  "dashboard", "media", "search", "artist", "release", "mix", "playlist", "share",
] as const

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: {
        common: enCommon, nav: enNav, profile: enProfile, auth: enAuth,
        settings: enSettings, player: enPlayer, dashboard: enDashboard,
        media: enMedia, search: enSearch, artist: enArtist, release: enRelease,
        mix: enMix, playlist: enPlaylist, share: enShare,
      },
      ru: {
        common: ruCommon, nav: ruNav, profile: ruProfile, auth: ruAuth,
        settings: ruSettings, player: ruPlayer, dashboard: ruDashboard,
        media: ruMedia, search: ruSearch, artist: ruArtist, release: ruRelease,
        mix: ruMix, playlist: ruPlaylist, share: ruShare,
      },
    },
    fallbackLng: "en",
    supportedLngs: SUPPORTED_LANGUAGES,
    ns: NAMESPACES,
    defaultNS: "common",
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
      caches: ["localStorage"],
    },
  })

export default i18n
