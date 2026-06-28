import ProfileButton from "@/components/profile/ProfileButton"

export default function TopBar() {
  return (
    <header className="h-14 shrink-0 flex items-center justify-between px-4 border-b border-border bg-background/80 backdrop-blur-sm z-10">
      <span className="text-primary font-bold text-lg tracking-tight select-none">discocs</span>
      <ProfileButton />
    </header>
  )
}
