import { useState } from "react"
import { Link, useNavigate } from "react-router"
import { useQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import type { TFunction } from "i18next"
import { Settings, User, LogOut } from "lucide-react"
import { cn } from "@/lib/utils"
import { useNavidromeStatus } from "@/api/hooks/useNavidromeStatus"
import { usePeople } from "@/api/hooks/usePeople"
import ArtworkImage from "@/components/media/ArtworkImage"
import { avatarUrl } from "@/lib/avatars"
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

const IDENTITY_AVATAR_SIZE = 32

/**
 * Who is signed in: the profile avatar and the login as a link to the profile.
 * Lives inside the popover content, so the people list (the avatar's source)
 * is only requested while the popover is open.
 */
function PopoverIdentity({ username, onNavigate }: {
  readonly username: string | null
  readonly onNavigate: () => void
}) {
  const { t } = useTranslation("profile")
  const { data: people } = usePeople()
  const avatar = username
    ? avatarUrl(people?.items.find((person) => person.username === username)?.avatar)
    : null
  const name = username ?? t("unknownUser")

  return (
    <div className="flex items-center gap-2 border-b border-border/50 pb-3">
      <ArtworkImage
        src={avatar}
        alt={name}
        size={IDENTITY_AVATAR_SIZE}
        className="rounded-full"
        fallbackLetter={name[0]?.toUpperCase()}
      />
      <div className="min-w-0">
        {username ? (
          <Link
            to={`/u/${encodeURIComponent(username)}`}
            onClick={onNavigate}
            title={t("myProfile")}
            className="block truncate text-sm font-medium hover:underline"
          >
            {username}
          </Link>
        ) : (
          <p className="truncate text-sm font-medium">{name}</p>
        )}
        <p className="text-xs text-muted-foreground">{t("signedIn")}</p>
      </div>
    </div>
  )
}

export default function ProfileButton({ mobile = false }: { readonly mobile?: boolean }) {
  const { t } = useTranslation("profile")
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
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

  const username = session?.username ?? null
  const { dotColor, statusLabel } = navidromeStatusUi(status, isLoading, t)
  const logoutLabel = session?.username
    ? t("signOut.withUser", { username: session.username })
    : t("signOut.default")
  const activeLanguage = userSettings?.language ?? "en"

  return (
    <Popover open={open} onOpenChange={setOpen}>
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
          <PopoverIdentity username={username} onNavigate={() => setOpen(false)} />
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
          {username && (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() => {
                setOpen(false)
                navigate(`/u/${encodeURIComponent(username)}`)
              }}
            >
              <User size={14} className="mr-2" />
              {t("myProfile")}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => {
              setOpen(false)
              navigate("/settings")
            }}
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
