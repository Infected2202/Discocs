import { describe, expect, it, vi } from "vitest"
import { decodeTimeline, TimelineDecodeError } from "./decoder"

function fixture() {
  const payload = new ArrayBuffer(20)
  new Int16Array(payload, 0, 2).set([-32767, -16384])
  new Int16Array(payload, 4, 2).set([32767, 16384])
  new Uint16Array(payload, 8, 2).set([65535, 32768])
  new Uint16Array(payload, 12, 2).set([100, 200])
  new Uint16Array(payload, 16, 2).set([300, 400])
  const descriptor = (offset: number, dtype: "int16" | "uint16") => ({
    offset, length: 2, dtype, scale: dtype === "int16" ? 1 / 32767 : 1 / 65535, unit: "normalized",
  })
  return {
    payload,
    manifest: {
      schema_version: 1,
      pack_name: "timeline_foundation",
      extractor: "timeline_foundation_v1",
      duration_seconds: 2,
      waveform: {
        sample_rate: 8,
        base_bucket_samples: 4,
        pyramid_factor: 4,
        levels: [{
          level: 0,
          bucket_samples: 4,
          bucket_count: 2,
          arrays: {
            minimum: descriptor(0, "int16"),
            maximum: descriptor(4, "int16"),
            low: descriptor(8, "uint16"),
            mid: descriptor(12, "uint16"),
            high: descriptor(16, "uint16"),
          },
        }],
      },
      payload: { byte_length: 20, sha256: "fixture-sha", endianness: "little", descriptor_alignment: 4 },
    },
  }
}

describe("timeline v1 decoder", () => {
  it("round-trips aligned little-endian typed views without copying payload data", async () => {
    const { manifest, payload } = fixture()
    const decoded = await decodeTimeline(manifest, payload, vi.fn().mockResolvedValue("fixture-sha"))

    expect(decoded.levels[0]?.bucketDurationSeconds).toBe(0.5)
    expect(Array.from(decoded.levels[0]?.minimum ?? [])).toEqual([-32767, -16384])
    expect(Array.from(decoded.levels[0]?.high ?? [])).toEqual([300, 400])
    expect(decoded.levels[0]?.minimum.buffer).toBe(payload)
  })

  it.each([
    ["version", (manifest: Record<string, unknown>) => { manifest.schema_version = 2 }, "schema version"],
    ["length", (manifest: Record<string, unknown>) => {
      ;(manifest.payload as Record<string, unknown>).byte_length = 19
    }, "length mismatch"],
    ["dtype", (manifest: Record<string, unknown>) => {
      const waveform = manifest.waveform as { levels: Array<{ arrays: { low: { dtype: string } } }> }
      waveform.levels[0]!.arrays.low.dtype = "int64"
    }, "array descriptor"],
  ])("rejects corrupt %s metadata", async (_name, corrupt, message) => {
    const { manifest, payload } = fixture()
    corrupt(manifest as unknown as Record<string, unknown>)
    await expect(decodeTimeline(manifest, payload, vi.fn())).rejects.toThrow(message)
  })

  it("rejects a corrupt checksum before exposing decoded arrays", async () => {
    const { manifest, payload } = fixture()
    await expect(decodeTimeline(manifest, payload, vi.fn().mockResolvedValue("wrong"))).rejects.toEqual(
      new TimelineDecodeError("payload checksum mismatch"),
    )
  })
})
