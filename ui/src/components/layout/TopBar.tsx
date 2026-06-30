import { useState } from "react"
import { Menu } from "lucide-react"
import ProfileButton from "@/components/profile/ProfileButton"
import MobileNav from "./MobileNav"

export default function TopBar() {
  const [navOpen, setNavOpen] = useState(false)

  return (
    <>
      <header
        className="h-14 shrink-0 flex items-center justify-between px-4 border-b border-border bg-background/80 backdrop-blur-sm z-10"
        style={{ paddingTop: "var(--sa-top)" }}
      >
        {/* Mobile: hamburger; Desktop: logo */}
        <button
          className="md:hidden p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
          onClick={() => setNavOpen(true)}
        >
          <Menu size={20} />
        </button>
        <span className="text-primary font-bold text-lg tracking-widest select-none hidden md:block uppercase" style={{ fontFamily: "'Onest Variable', sans-serif" }}>discocs</span>

        <ProfileButton />
      </header>

      <MobileNav open={navOpen} onClose={() => setNavOpen(false)} />
    </>
  )
}
