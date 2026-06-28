import { NavLink } from "react-router"
import { Home, Search } from "lucide-react"
import { cn } from "@/lib/utils"
import ProfileButton from "@/components/profile/ProfileButton"

const NAV = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/search", label: "Search", icon: Search, end: false },
]

export default function MobileTabBar() {
  return (
    <nav className="md:hidden fixed bottom-[92px] left-0 right-0 z-30 flex items-center justify-around h-14 bg-card border-t border-border px-2">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            cn(
              "flex flex-col items-center gap-0.5 px-4 py-1.5 rounded-lg transition-colors",
              isActive
                ? "text-primary"
                : "text-muted-foreground hover:text-foreground",
            )
          }
        >
          <Icon size={20} strokeWidth={1.75} />
          <span className="text-[10px] font-medium">{label}</span>
        </NavLink>
      ))}
      <ProfileButton mobile />
    </nav>
  )
}
