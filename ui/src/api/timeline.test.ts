import { beforeEach, describe, expect, it, vi } from "vitest"
import { invalidateTimeline, loadTimeline } from "./timeline"

function fixture() {
  const payload = new ArrayBuffer(20)
  const descriptor = (offset: number, dtype: "int16" | "uint16") => ({ offset, length: 2, dtype, scale: 1, unit: "normalized" })
  return { payload, manifest: {
    schema_version: 1, pack_name: "timeline_foundation", extractor: "timeline_foundation_v1", duration_seconds: 2,
    waveform: { sample_rate: 8, base_bucket_samples: 4, pyramid_factor: 4, levels: [{
      level: 0, bucket_samples: 4, bucket_count: 2,
      arrays: {
        minimum: descriptor(0, "int16"), maximum: descriptor(4, "int16"),
        low: descriptor(8, "uint16"), mid: descriptor(12, "uint16"), high: descriptor(16, "uint16"),
      },
    }] },
    payload: { byte_length: 20, sha256: "00", endianness: "little", descriptor_alignment: 4 },
  } }
}

describe("timeline artifact client", () => {
  beforeEach(() => invalidateTimeline())

  it("fetches manifest and payload once and shares the decoded arrays", async () => {
    const { manifest, payload } = fixture()
    vi.stubGlobal("crypto", { subtle: { digest: vi.fn().mockResolvedValue(new Uint8Array([0]).buffer) } })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => manifest })
      .mockResolvedValueOnce({ ok: true, arrayBuffer: async () => payload })
    vi.stubGlobal("fetch", fetchMock)

    const [first, second] = await Promise.all([loadTimeline(7), loadTimeline(7)])

    expect(first.status).toBe("ready")
    expect(second.timeline).toBe(first.timeline)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("maps a missing manifest through durable batch status", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({ detail: "missing" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ items: [{ track_id: 8, status: "failed", error: "decode" }] }) }))

    await expect(loadTimeline(8)).resolves.toEqual({ status: "failed", message: "decode" })
  })
})
