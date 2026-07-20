import type {
  DecodedTimeline,
  TimelineArrayDescriptor,
  TimelineDtype,
  TimelineManifestV1,
  TimelineWaveformLevelManifest,
} from "./types"

const SCHEMA_VERSION = 1
const ALIGNMENT = 4
const PYRAMID_FACTOR = 4
const waveformFields = ["minimum", "maximum", "low", "mid", "high"] as const
const byteWidths: Readonly<Record<TimelineDtype, number>> = {
  int16: 2,
  uint16: 2,
  uint8: 1,
  float32: 4,
}

export class TimelineDecodeError extends Error {}

export type TimelineChecksum = (payload: ArrayBuffer) => Promise<string>

async function sha256(payload: ArrayBuffer): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", payload)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function parseDescriptor(value: unknown, payloadLength: number): TimelineArrayDescriptor {
  if (!isRecord(value)) throw new TimelineDecodeError("invalid array descriptor")
  const { offset, length, dtype, scale, unit } = value
  if (
    !Number.isInteger(offset) || Number(offset) < 0 || Number(offset) % ALIGNMENT !== 0 ||
    !Number.isInteger(length) || Number(length) < 0 ||
    typeof dtype !== "string" || !(dtype in byteWidths) ||
    typeof scale !== "number" || !Number.isFinite(scale) ||
    typeof unit !== "string"
  ) {
    throw new TimelineDecodeError("invalid array descriptor")
  }
  const typedDtype = dtype as TimelineDtype
  if (Number(offset) + Number(length) * byteWidths[typedDtype] > payloadLength) {
    throw new TimelineDecodeError("array descriptor exceeds payload")
  }
  return { offset: Number(offset), length: Number(length), dtype: typedDtype, scale, unit }
}

function parseLevel(value: unknown, payloadLength: number): TimelineWaveformLevelManifest {
  if (!isRecord(value) || !isRecord(value.arrays)) throw new TimelineDecodeError("invalid waveform level")
  const arrayValues = value.arrays
  if (!Number.isInteger(value.level) || !Number.isInteger(value.bucket_samples) || !Number.isInteger(value.bucket_count)) {
    throw new TimelineDecodeError("invalid waveform level dimensions")
  }
  const arrays = Object.fromEntries(
    waveformFields.map((field) => [field, parseDescriptor(arrayValues[field], payloadLength)]),
  ) as unknown as TimelineWaveformLevelManifest["arrays"]
  if (waveformFields.some((field) => arrays[field].length !== value.bucket_count)) {
    throw new TimelineDecodeError("waveform array length mismatch")
  }
  return {
    level: Number(value.level),
    bucket_samples: Number(value.bucket_samples),
    bucket_count: Number(value.bucket_count),
    arrays,
  }
}

function parseManifest(value: unknown, payloadLength: number): TimelineManifestV1 {
  if (!isRecord(value) || value.schema_version !== SCHEMA_VERSION) {
    throw new TimelineDecodeError("unsupported timeline schema version")
  }
  if (!isRecord(value.payload) || value.payload.byte_length !== payloadLength) {
    throw new TimelineDecodeError("payload length mismatch")
  }
  if (
    value.payload.endianness !== "little" || value.payload.descriptor_alignment !== ALIGNMENT ||
    typeof value.payload.sha256 !== "string"
  ) {
    throw new TimelineDecodeError("unsupported payload layout")
  }
  if (!isRecord(value.waveform) || !Array.isArray(value.waveform.levels)) {
    throw new TimelineDecodeError("missing waveform levels")
  }
  if (
    typeof value.duration_seconds !== "number" || value.duration_seconds <= 0 ||
    typeof value.waveform.sample_rate !== "number" ||
    typeof value.waveform.base_bucket_samples !== "number" ||
    value.waveform.pyramid_factor !== PYRAMID_FACTOR
  ) {
    throw new TimelineDecodeError("invalid timeline dimensions")
  }
  return {
    schema_version: 1,
    duration_seconds: value.duration_seconds,
    waveform: {
      sample_rate: value.waveform.sample_rate,
      base_bucket_samples: value.waveform.base_bucket_samples,
      pyramid_factor: 4,
      levels: value.waveform.levels.map((level) => parseLevel(level, payloadLength)),
    },
    payload: {
      byte_length: payloadLength,
      sha256: value.payload.sha256,
      endianness: "little",
      descriptor_alignment: 4,
    },
  }
}

function typedView(descriptor: TimelineArrayDescriptor, payload: ArrayBuffer): Int16Array | Uint16Array {
  if (descriptor.dtype === "int16") return new Int16Array(payload, descriptor.offset, descriptor.length)
  if (descriptor.dtype === "uint16") return new Uint16Array(payload, descriptor.offset, descriptor.length)
  throw new TimelineDecodeError(`waveform array cannot use ${descriptor.dtype}`)
}

export async function decodeTimeline(
  manifestValue: unknown,
  payload: ArrayBuffer,
  checksum: TimelineChecksum = sha256,
): Promise<DecodedTimeline> {
  const manifest = parseManifest(manifestValue, payload.byteLength)
  if (await checksum(payload) !== manifest.payload.sha256) {
    throw new TimelineDecodeError("payload checksum mismatch")
  }
  return {
    durationSeconds: manifest.duration_seconds,
    levels: manifest.waveform.levels.map((level) => ({
      bucketDurationSeconds: level.bucket_samples / manifest.waveform.sample_rate,
      minimum: typedView(level.arrays.minimum, payload) as Int16Array,
      maximum: typedView(level.arrays.maximum, payload) as Int16Array,
      low: typedView(level.arrays.low, payload) as Uint16Array,
      mid: typedView(level.arrays.mid, payload) as Uint16Array,
      high: typedView(level.arrays.high, payload) as Uint16Array,
    })),
  }
}
