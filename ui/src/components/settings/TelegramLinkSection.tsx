import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { ExternalLink, Loader2, Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  startTelegramLink,
  TELEGRAM_LINK_QUERY_KEY,
  unlinkTelegram,
  useTelegramLink,
} from "@/api/telegram"

/**
 * Linking runs as a deep link: the backend mints a one-shot token, the user
 * opens the bot and presses Start, and the bot redeems the token. The link is
 * shown as an anchor the user taps rather than opened from the click handler:
 * a window.open after an awaited request is eaten by popup blockers.
 */
export default function TelegramLinkSection() {
  const { t } = useTranslation("settings")
  const queryClient = useQueryClient()
  const { data: status, isLoading, isError } = useTelegramLink()
  const [deepLink, setDeepLink] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (isLoading) return <Loader2 size={14} className="animate-spin text-muted-foreground" />
  if (isError || !status?.enabled) return null

  const refresh = () => queryClient.invalidateQueries({ queryKey: TELEGRAM_LINK_QUERY_KEY })

  async function connect() {
    setPending(true)
    setError(null)
    try {
      setDeepLink((await startTelegramLink()).url)
    } catch {
      setError(t("telegram.connectError"))
    } finally {
      setPending(false)
    }
  }

  async function disconnect() {
    setPending(true)
    setError(null)
    try {
      await unlinkTelegram()
      setDeepLink(null)
      await refresh()
    } catch {
      setError(t("telegram.unlinkError"))
    } finally {
      setPending(false)
    }
  }

  const username = status.link?.telegram_username
  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">{t("telegram.heading")}</h2>
        <p className="text-sm text-muted-foreground mt-0.5">{t("telegram.description")}</p>
      </div>
      <div className="space-y-3 rounded-md bg-muted px-4 py-3">
        {status.linked ? (
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm">
              {username ? t("telegram.linkedAs", { username }) : t("telegram.linked")}
            </p>
            <Button variant="outline" size="sm" onClick={disconnect} disabled={pending}>
              {t("telegram.unlink")}
            </Button>
          </div>
        ) : deepLink ? (
          <div className="space-y-2">
            <a
              href={deepLink}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-sm font-medium text-primary hover:underline"
            >
              <ExternalLink size={14} />
              {t("telegram.openBot")}
            </a>
            <p className="text-xs text-muted-foreground">{t("telegram.openBotHint")}</p>
            <Button variant="outline" size="sm" onClick={refresh}>
              {t("telegram.checkLink")}
            </Button>
          </div>
        ) : (
          <Button size="sm" onClick={connect} disabled={pending}>
            {pending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            {t("telegram.connect")}
          </Button>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>
    </section>
  )
}
