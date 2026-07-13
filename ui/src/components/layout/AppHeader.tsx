import { useState, useEffect } from "react"
import { useNavigate, useSearchParams } from "react-router"
import { useTranslation } from "react-i18next"
import { Search } from "lucide-react"
import ProfileButton from "@/components/profile/ProfileButton"

export default function AppHeader() {
  const { t } = useTranslation("nav")
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const urlQuery = searchParams.get("q") ?? ""
  const [query, setQuery] = useState(urlQuery)

  useEffect(() => { setQuery(urlQuery) }, [urlQuery])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const q = query.trim()
    if (q) navigate(`/search?q=${encodeURIComponent(q)}`)
  }

  return (
    <div className="shrink-0 flex items-center gap-4 sm:gap-6 px-4 sm:px-6 py-3">
      <span
        className="text-primary font-bold text-2xl tracking-tight select-none shrink-0 cursor-pointer"
        style={{ fontFamily: "'Onest Variable', sans-serif" }}
        onClick={() => navigate("/")}
      >
        discocs
      </span>

      <form onSubmit={handleSubmit} className="flex-1 max-w-xl">
        <div className="relative">
          <Search
            size={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            className="w-full rounded-lg bg-muted/50 pl-9 pr-4 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
      </form>

      <div className="ml-auto shrink-0">
        <ProfileButton />
      </div>
    </div>
  )
}
