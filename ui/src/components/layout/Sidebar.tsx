import { NavLink } from "react-router"
import { Home, Search } from "lucide-react"
import { cn } from "@/lib/utils"
import { useUIStore } from "@/store/uiStore"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"

const NAV = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/search", label: "Search", icon: Search, end: false },
]

export default function Sidebar() {
  const collapsed = useUIStore((s) => s.sidebarCollapsed)

  return (
    <aside
      className={cn(
        "shrink-0 flex flex-col bg-sidebar border-r border-sidebar-border h-full pb-[92px] transition-[width] duration-200",
        collapsed ? "w-16" : "w-[220px]",
      )}
    >
      <nav className="flex-1 px-2 py-2 space-y-0.5">
        <TooltipProvider>
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <Tooltip key={to}>
              <TooltipTrigger asChild>
                <NavLink
                  to={to}
                  end={end}
                  title={collapsed ? label : undefined}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-3 rounded-md text-sm font-medium transition-colors",
                      collapsed ? "justify-center px-0 py-2.5" : "px-3 py-2",
                      isActive
                        ? "bg-sidebar-accent text-foreground"
                        : "text-sidebar-foreground/70 hover:text-sidebar-foreground hover:bg-sidebar-accent/50",
                    )
                  }
                >
                  <Icon size={18} strokeWidth={1.75} className="shrink-0" />
                  {!collapsed && <span>{label}</span>}
                </NavLink>
              </TooltipTrigger>
              {collapsed && (
                <TooltipContent side="right">{label}</TooltipContent>
              )}
            </Tooltip>
          ))}
        </TooltipProvider>
      </nav>
    </aside>
  )
}
