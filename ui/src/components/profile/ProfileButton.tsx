import { useState } from "react"
import { useNavigate } from "react-router"
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import type { TFunction } from "i18next"
import { Settings, User, LogOut } from "lucide-react"
import { cn } from "@/lib/utils"
import { useNavidromeStatus } from "@/api/hooks/useNavidromeStatus"
import { Button } from "@/components/ui/button"
import {
  Popover, PopoverContent, PopoverTrigger,
} from "@/components/ui/popover"
import { getSession, logout } from "@/api/auth"
import { redirectToLogin } from "@/lib/authRedirect"
import { useUserSettings, useUpdateUserSettings } from "@/api/hooks/useUserSettings"
import { SUPPORTED_LANGUAGES, LANGUAGE_NAMES } from "@/i18n"

function navidromeStatusUi(status: string, isLoading: boolean, t: TFunction<"profile">) {
  if (isLoading) {
    return {
      dotColor: "bg-muted-foreground",
      statusLabel: t("navidrome.checking"),
    }
  }
  if (status === "connected") {
    return {
      dotColor: "bg-green-500",
      statusLabel: t("navidrome.connected"),
    }
  }
  return {
    dotColor: "bg-red-500",
    statusLabel: t("navidrome.disconnected"),
  }
}

export default function ProfileButton({ mobile = false }: { readonly mobile?: boolean }) {
  const { t } = useTranslation("profile")
  const navigate = useNavigate()
  const [logoutPending, setLogoutPending] = useState(false)
  const [logoutFailed, setLogoutFailed] = useState(false)
  const { status, isLoading } = useNavidromeStatus()
  const { data: session } = useQuery({
    queryKey: ["auth", "session"],
    queryFn: getSession,
    retry: false,
    staleTime: 60_000,
  })
  const { data: userSettings } = useUserSettings()
  const { mutate: setLanguage, isPending: languagePending } = useUpdateUserSettings()

  async function handleLogout() {
    if (logoutPending) return
    setLogoutPending(true)
    setLogoutFailed(false)
    try {
      await logout()
      redirectToLogin()
    } catch {
      // Do not pretend that the server-side session was revoked. Otherwise
      // LoginPage sees the still-valid cookie and immediately returns to the
      // app, which looks like an automatic re-login.
      setLogoutFailed(true)
      setLogoutPending(false)
    }
  }

  const { dotColor, statusLabel } = navidromeStatusUi(status, isLoading, t)
  const logoutLabel = session?.username
    ? t("signOut.withUser", { username: session.username })
    : t("signOut.default")
  const activeLanguage = userSettings?.language ?? "en"

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="relative flex h-8 w-8 items-center justify-center rounded-full transition-colors hover:bg-muted/40"
          title={session?.username ? t("trigger.titleWithUser", { username: session.username }) : t("trigger.title")}
        >
          <User size={16} />
          <span className={cn("absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border-2 border-background", dotColor)} />
        </button>
      </PopoverTrigger>
      <PopoverContent align={mobile ? "center" : "end"} className="w-56 p-3">
        <div className="space-y-3">
          <div className="flex items-center gap-2 border-b border-border/50 pb-3">
            <User size={16} className="text-muted-foreground" />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{session?.username ?? t("unknownUser")}</p>
              <p className="text-xs text-muted-foreground">{t("signedIn")}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={cn("h-2 w-2 shrink-0 rounded-full", dotColor)} />
            <span className="text-sm">{statusLabel}</span>
          </div>
          <div className="space-y-1.5">
            <p className="text-xs text-muted-foreground">{t("language")}</p>
            <div className="flex gap-1.5">
              {SUPPORTED_LANGUAGES.map((lang) => (
                <Button
                  key={lang}
                  variant={activeLanguage === lang ? "default" : "outline"}
                  size="sm"
                  className="flex-1"
                  disabled={languagePending}
                  onClick={() => setLanguage({ language: lang })}
                >
                  {LANGUAGE_NAMES[lang]}
                </Button>
              ))}
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => navigate("/settings")}
          >
            <Settings size={14} className="mr-2" />
            {t("openSettings")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="w-full text-muted-foreground"
            onClick={handleLogout}
            disabled={logoutPending}
          >
            <LogOut size={14} className="mr-2" />
            {logoutPending ? t("signOut.pending") : logoutLabel}
          </Button>
          {logoutFailed && (
            <p className="text-xs text-destructive" role="alert">
              {t("signOut.failed")}
            </p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
