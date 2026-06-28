import { useEffect } from "react"
import { NavLink } from "react-router"
import { Home, Search, X } from "lucide-react"
import { cn } from "@/lib/utils"
import ProfileButton from "@/components/profile/ProfileButton"

const NAV = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/search", label: "Search", icon: Search, end: false },
]

interface MobileNavProps {
  open: boolean
  onClose: () => void
}

export default function MobileNav({ open, onClose }: MobileNavProps) {
  useEffect(() => {
    if (open) document.body.style.overflow = "hidden"
    else document.body.style.overflow = ""
    return () => { document.body.style.overflow = "" }
  }, [open])

  return (
    <div
      className={cn(
        "fixed inset-0 z-50 md:hidden flex flex-col bg-background transition-transform duration-300 ease-out",
        open ? "translate-x-0" : "-translate-x-full pointer-events-none",
      )}
    >
      {/* Header */}
      <div className="h-14 flex items-center justify-between px-4 border-b border-border shrink-0" style={{ paddingTop: "var(--sa-top)" }}>
        <span className="text-primary font-bold text-lg tracking-tight select-none">discocs</span>
        <button onClick={onClose} className="p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors">
          <X size={20} />
        </button>
      </div>

      {/* Nav links */}
      <nav className="flex-1 px-4 py-4 space-y-1">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onClose}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 px-3 py-3 rounded-lg text-base font-medium transition-colors",
                isActive
                  ? "bg-sidebar-accent text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/60",
              )
            }
          >
            <Icon size={20} strokeWidth={1.75} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Profile at bottom */}
      <div className="px-4 py-4 border-t border-border shrink-0" style={{ paddingBottom: "var(--sa-bottom)" }}>
        <ProfileButton mobile />
      </div>
    </div>
  )
}
