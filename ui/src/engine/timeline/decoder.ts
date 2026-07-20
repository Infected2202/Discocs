import type {
  DecodedTimeline,
  TimelineArrayDescriptor,
  TimelineDtype,
  TimelineManifestV2,
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

function parseRhythm(value: unknown, payloadLength: number, durationSeconds: number): TimelineManifestV2["rhythm"] {
  if (!isRecord(value) || !isRecord(value.arrays)) {
    throw new TimelineDecodeError("missing rhythm series")
  }
  const beats = parseDescriptor(value.arrays.beats, payloadLength)
  const localTempo = parseDescriptor(value.arrays.local_tempo, payloadLength)
  const scalarLayoutValid = (
    typeof value.bpm === "number" && Number.isFinite(value.bpm) && value.bpm >= 0 &&
    typeof value.confidence === "number" && Number.isFinite(value.confidence) &&
    typeof value.coverage_seconds === "number" && Number.isFinite(value.coverage_seconds) &&
    value.coverage_seconds > 0 && value.coverage_seconds <= durationSeconds
  )
  if (beats.dtype !== "float32" || localTempo.dtype !== "float32" || beats.length !== localTempo.length || !scalarLayoutValid) {
    throw new TimelineDecodeError("invalid rhythm series")
  }
  return {
    bpm: value.bpm as number,
    confidence: value.confidence as number,
    coverage_seconds: value.coverage_seconds as number,
    arrays: { beats, local_tempo: localTempo },
  }
}

function parseManifest(value: unknown, payloadLength: number): TimelineManifestV2 {
  if (!isRecord(value) || value.schema_version !== SCHEMA_VERSION) {
    throw new TimelineDecodeError("unsupported timeline schema version")
  }
  if (value.pack_name !== "timeline_foundation" || value.extractor !== "timeline_foundation_v2") {
    throw new TimelineDecodeError("unsupported timeline pack or extractor")
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
  const rhythm = parseRhythm(value.rhythm, payloadLength, value.duration_seconds)
  return {
    schema_version: 1,
    pack_name: "timeline_foundation",
    extractor: "timeline_foundation_v2",
    duration_seconds: value.duration_seconds,
    waveform: {
      sample_rate: value.waveform.sample_rate,
      base_bucket_samples: value.waveform.base_bucket_samples,
      pyramid_factor: 4,
      levels: value.waveform.levels.map((level) => parseLevel(level, payloadLength)),
    },
    rhythm,
    payload: {
      byte_length: payloadLength,
      sha256: value.payload.sha256,
      endianness: "little",
      descriptor_alignment: 4,
    },
  }
}

function typedView(descriptor: TimelineArrayDescriptor, payload: ArrayBuffer): Int16Array | Uint16Array | Float32Array {
  if (descriptor.dtype === "int16") return new Int16Array(payload, descriptor.offset, descriptor.length)
  if (descriptor.dtype === "uint16") return new Uint16Array(payload, descriptor.offset, descriptor.length)
  if (descriptor.dtype === "float32") return new Float32Array(payload, descriptor.offset, descriptor.length)
  throw new TimelineDecodeError(`timeline array cannot use ${descriptor.dtype}`)
}

function validateRhythmPayload(beats: Float32Array, localTempo: Float32Array, durationSeconds: number): void {
  for (let index = 0; index < beats.length; index += 1) {
    const beat = beats[index]
    const tempo = localTempo[index]
    const ordered = index === 0 || beat >= beats[index - 1]
    if (!Number.isFinite(beat) || beat < 0 || beat > durationSeconds || !ordered || !Number.isFinite(tempo) || tempo <= 0) {
      throw new TimelineDecodeError("invalid rhythm payload")
    }
  }
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
  const beats = typedView(manifest.rhythm.arrays.beats, payload) as Float32Array
  const localTempo = typedView(manifest.rhythm.arrays.local_tempo, payload) as Float32Array
  validateRhythmPayload(beats, localTempo, manifest.duration_seconds)
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
    bpm: manifest.rhythm.bpm,
    beatConfidence: manifest.rhythm.confidence,
    rhythmCoverageSeconds: manifest.rhythm.coverage_seconds,
    beats,
    localTempo,
  }
}
