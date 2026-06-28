import ProfileButton from "@/components/profile/ProfileButton"

export default function TopBar() {
  return (
    <header className="h-14 shrink-0 flex items-center justify-end px-6 border-b border-border bg-background/80 backdrop-blur-sm sticky top-0 z-10">
      <ProfileButton />
    </header>
  )
}
