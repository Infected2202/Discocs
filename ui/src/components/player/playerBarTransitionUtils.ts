interface PreloadableImage {
  src: string
  complete: boolean
  naturalWidth: number
  onload: (() => void) | null
  onerror: (() => void) | null
  decode?: () => Promise<void>
}

export async function preloadArtworkImage(
  artworkUrl: string | null | undefined,
  createImage: () => PreloadableImage = () => new Image()
): Promise<void> {
  if (!artworkUrl) return

  const image = createImage()

  await new Promise<void>((resolve) => {
    let settled = false

    const finish = async () => {
      if (settled) return
      settled = true

      try {
        await image.decode?.()
      } catch {
        // ArtworkImage will show its regular fallback if decoding failed.
      }

      resolve()
    }

    image.onload = finish
    image.onerror = finish
    image.src = artworkUrl

    if (image.complete) {
      void finish()
    }
  })
}
