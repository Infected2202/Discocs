import { NavLink } from "react-router"
import { Home, Search } from "lucide-react"
import { cn } from "@/lib/utils"

const NAV = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/search", label: "Search", icon: Search, end: false },
]

export default function Sidebar() {
  return (
    <aside className="w-[220px] shrink-0 flex flex-col bg-sidebar border-r border-sidebar-border h-full pb-[92px]">
      {/* Logo */}
      <div className="h-14 flex items-center px-5 shrink-0">
        <span className="text-primary font-bold text-lg tracking-tight">discocs</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-2 space-y-0.5">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                isActive
                  ? "bg-sidebar-accent text-foreground"
                  : "text-sidebar-foreground/70 hover:text-sidebar-foreground hover:bg-sidebar-accent/50",
              )
            }
          >
            <Icon size={18} strokeWidth={1.75} />
            {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
