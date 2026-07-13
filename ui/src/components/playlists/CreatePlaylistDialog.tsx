import { useEffect, useState, type FormEvent } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { useUIStore, type CreatePlaylistValues } from "@/store/uiStore"
import { createPlaylist, updatePlaylist } from "@/api/playlists"

const FIELD_CLASS =
  "w-full rounded-md bg-muted px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"

export default function CreatePlaylistDialog() {
  const options = useUIStore((s) => s.createPlaylistOptions)
  const close = useUIStore((s) => s.closeCreatePlaylist)
  const queryClient = useQueryClient()

  const editing = options?.playlist ?? null
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [visibility, setVisibility] = useState<"public" | "private">("private")

  // Reset the form each time the dialog opens with new options.
  useEffect(() => {
    if (!options) return
    setTitle(options.playlist?.title ?? options.defaultTitle ?? "")
    setDescription(options.playlist?.description ?? options.defaultDescription ?? "")
    const sourceVisibility = options.playlist?.source?.visibility
    setVisibility(sourceVisibility === "public" ? "public" : "private")
  }, [options])

  const { mutate: submit, isPending } = useMutation({
    mutationFn: async (values: CreatePlaylistValues) => {
      if (options?.onSubmit) {
        await options.onSubmit(values)
        return
      }
      if (editing) {
        await updatePlaylist(editing.id, {
          title: values.title,
          description: values.description || null,
        })
        return
      }
      await createPlaylist({
        title: values.title,
        description: values.description || null,
        visibility: values.visibility,
        track_ids: options?.trackIds ?? [],
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["playlists"] })
      if (editing) {
        queryClient.invalidateQueries({ queryKey: ["playlist", editing.id] })
      }
      close()
    },
  })

  const trimmedTitle = title.trim()

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!trimmedTitle || isPending) return
    submit({ title: trimmedTitle, description: description.trim(), visibility })
  }

  return (
    <Dialog open={options !== null} onOpenChange={(open) => { if (!open) close() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editing ? "Edit playlist" : "New playlist"}</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground">Name</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Playlist name"
              className={FIELD_CLASS}
              autoFocus
            />
          </label>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground">Description</span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
              rows={3}
              className={`${FIELD_CLASS} resize-none`}
            />
          </label>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground">Visibility</span>
            <select
              value={visibility}
              onChange={(e) => setVisibility(e.target.value as "public" | "private")}
              className={FIELD_CLASS}
            >
              <option value="private">Private</option>
              <option value="public">Public</option>
            </select>
          </label>

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" disabled={!trimmedTitle || isPending}>
              {editing ? "Save" : "Create"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
