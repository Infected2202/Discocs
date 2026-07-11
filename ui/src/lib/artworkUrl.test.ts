import { describe, it, expect } from "vitest"
import { hiresArtworkUrl } from "./artworkUrl"

describe("hiresArtworkUrl()", () => {
  it("добавляет size, если его ещё нет в url", () => {
    expect(hiresArtworkUrl("/api/v1/tracks/1/cover")).toBe("/api/v1/tracks/1/cover?size=600")
  })

  it("использует переданный size вместо дефолтного 600", () => {
    expect(hiresArtworkUrl("/api/v1/tracks/1/cover", 512)).toBe("/api/v1/tracks/1/cover?size=512")
  })

  it("заменяет уже существующий size, а не дублирует его", () => {
    expect(hiresArtworkUrl("/api/v1/tracks/1/cover?size=96", 512)).toBe("/api/v1/tracks/1/cover?size=512")
  })

  it("дописывает size через & если в url уже есть другие query-параметры", () => {
    expect(hiresArtworkUrl("/api/v1/tracks/1/cover?foo=bar")).toBe("/api/v1/tracks/1/cover?foo=bar&size=600")
  })

  it("возвращает undefined для отсутствующего url", () => {
    expect(hiresArtworkUrl(null)).toBeUndefined()
    expect(hiresArtworkUrl(undefined)).toBeUndefined()
  })
})
