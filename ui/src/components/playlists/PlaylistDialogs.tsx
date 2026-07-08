import AddToPlaylistDialog from "./AddToPlaylistDialog"
import CreatePlaylistDialog from "./CreatePlaylistDialog"

/** Mounted once in AppShell; open state lives in uiStore. */
export default function PlaylistDialogs() {
  return (
    <>
      <AddToPlaylistDialog />
      <CreatePlaylistDialog />
    </>
  )
}
