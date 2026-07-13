import { useEffect, useState, type FormEvent } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
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
  const { t } = useTranslation("playlist")
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
          visibility: values.visibility,
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
          <DialogTitle>{editing ? t("createDialog.editTitle") : t("createDialog.createTitle")}</DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground">{t("createDialog.nameLabel")}</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("createDialog.namePlaceholder")}
              className={FIELD_CLASS}
              autoFocus
            />
          </label>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground">{t("createDialog.descriptionLabel")}</span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("createDialog.descriptionPlaceholder")}
              rows={3}
              className={`${FIELD_CLASS} resize-none`}
            />
          </label>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground">{t("createDialog.visibilityLabel")}</span>
            <select
              value={visibility}
              onChange={(e) => setVisibility(e.target.value as "public" | "private")}
              className={FIELD_CLASS}
            >
              <option value="private">{t("createDialog.visibilityPrivate")}</option>
              <option value="public">{t("createDialog.visibilityPublic")}</option>
            </select>
          </label>

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" onClick={close}>
              {t("actions.cancel", { ns: "common" })}
            </Button>
            <Button type="submit" disabled={!trimmedTitle || isPending}>
              {editing ? t("actions.save", { ns: "common" }) : t("createDialog.create")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
