import { useEffect, useState } from "react"
import { useLocation, useNavigate } from "react-router"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { User, Lock, ArrowRight, Loader2 } from "lucide-react"
import PlasmaFBM from "@/components/player/PlasmaFBM"
import { Button } from "@/components/ui/button"
import { getSession, login } from "@/api/auth"
import { ApiError } from "@/api/client"
import { SUPPORTED_LANGUAGES } from "@/i18n"
import { cn } from "@/lib/utils"

export default function LoginPage() {
  const { t, i18n } = useTranslation("auth")
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Where to return after login — RequireAuth passes the page it bounced from.
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? "/"

  // The session may still be perfectly valid (e.g. we got here because of a
  // transient network error) — check once and skip the form entirely.
  const { data: session } = useQuery({
    queryKey: ["auth", "session"],
    queryFn: getSession,
    staleTime: 0,
    retry: false,
  })
  useEffect(() => {
    if (session?.authenticated) navigate(from, { replace: true })
  }, [session?.authenticated, from, navigate])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (submitting) return
    setError(null)
    setSubmitting(true)
    try {
      const result = await login(username, password)
      // Prime the session cache so RequireAuth doesn't read a stale
      // "unauthenticated" entry and bounce us straight back to /login.
      queryClient.setQueryData(["auth", "session"], {
        authenticated: true,
        username: result.username,
        enabled: true,
      })
      navigate(from, { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError(t("errors.tooManyAttempts"))
      } else if (err instanceof ApiError && err.status === 401) {
        setError(t("errors.invalidCredentials"))
      } else {
        setError(t("errors.serviceUnavailable"))
      }
      setSubmitting(false)
    }
  }

  return (
    <div className="relative h-svh w-full overflow-hidden bg-background">
      {/* Plasma */}
      <div className="fixed inset-0 pointer-events-none z-0" style={{ filter: "blur(4px)" }}>
        <PlasmaFBM active accent="#3b6bff" speed={0.7} scale={1.0} opacity={0.9} />
      </div>

      {/* Language toggle */}
      <div className="fixed right-4 top-4 z-[2] flex items-center gap-1 text-xs">
        {SUPPORTED_LANGUAGES.map((lang, i) => (
          <span key={lang} className="flex items-center gap-1">
            {i > 0 && <span className="text-muted-foreground/40">|</span>}
            <button
              type="button"
              onClick={() => void i18n.changeLanguage(lang)}
              aria-pressed={i18n.resolvedLanguage === lang}
              className={cn(
                "uppercase transition-colors",
                i18n.resolvedLanguage === lang
                  ? "font-medium text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {lang}
            </button>
          </span>
        ))}
      </div>

      {/* Centered card */}
      <div className="relative z-[1] flex h-full items-center justify-center px-6">
        <form
          onSubmit={handleSubmit}
          className="w-full max-w-[320px] rounded-3xl border border-border/40 bg-background/40 p-8 shadow-2xl backdrop-blur-xl"
        >
          <div className="mb-7 text-center text-xl font-semibold tracking-tight text-foreground">
            discocs
          </div>

          <div className="flex flex-col gap-3">
            <Field
              icon={<User className="size-4" />}
              type="text"
              placeholder={t("usernamePlaceholder")}
              autoComplete="username"
              value={username}
              onChange={setUsername}
              ariaLabel={t("usernameAriaLabel")}
            />
            <Field
              icon={<Lock className="size-4" />}
              type="password"
              placeholder={t("passwordPlaceholder")}
              autoComplete="current-password"
              value={password}
              onChange={setPassword}
              ariaLabel={t("passwordAriaLabel")}
            />
          </div>

          {error && (
            <p className="mt-3 text-center text-sm text-destructive" role="alert">
              {error}
            </p>
          )}

          <Button
            type="submit"
            size="lg"
            disabled={submitting || !username || !password}
            className="mt-5 w-full"
          >
            {submitting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <>
                {t("signIn")}
                <ArrowRight className="size-4" />
              </>
            )}
          </Button>
        </form>
      </div>
    </div>
  )
}

function Field({
  icon,
  type,
  placeholder,
  autoComplete,
  value,
  onChange,
  ariaLabel,
}: {
  icon: React.ReactNode
  type: string
  placeholder: string
  autoComplete: string
  value: string
  onChange: (v: string) => void
  ariaLabel: string
}) {
  return (
    <div className="flex items-center gap-2.5 rounded-2xl border border-border/50 bg-background/50 px-3.5 py-2.5 focus-within:border-ring">
      <span className="text-muted-foreground">{icon}</span>
      <input
        type={type}
        placeholder={placeholder}
        autoComplete={autoComplete}
        aria-label={ariaLabel}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
      />
    </div>
  )
}
