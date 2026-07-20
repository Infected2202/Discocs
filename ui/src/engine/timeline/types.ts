export type TimelineDtype = "int16" | "uint16" | "uint8" | "float32"

export interface TimelineArrayDescriptor {
  readonly offset: number
  readonly length: number
  readonly dtype: TimelineDtype
  readonly scale: number
  readonly unit: string
}

export interface TimelineWaveformLevelManifest {
  readonly level: number
  readonly bucket_samples: number
  readonly bucket_count: number
  readonly arrays: Readonly<Record<"minimum" | "maximum" | "low" | "mid" | "high", TimelineArrayDescriptor>>
}

export interface TimelineManifestV2 {
  readonly schema_version: 1
  readonly pack_name: "timeline_foundation"
  readonly extractor: "timeline_foundation_v2"
  readonly duration_seconds: number
  readonly waveform: {
    readonly sample_rate: number
    readonly base_bucket_samples: number
    readonly pyramid_factor: 4
    readonly levels: readonly TimelineWaveformLevelManifest[]
  }
  readonly rhythm: {
    readonly bpm: number
    readonly confidence: number
    readonly coverage_seconds: number
    readonly arrays: Readonly<Record<"beats" | "local_tempo", TimelineArrayDescriptor>>
  }
  readonly payload: {
    readonly byte_length: number
    readonly sha256: string
    readonly endianness: "little"
    readonly descriptor_alignment: 4
  }
}

export interface DecodedTimelineLevel {
  readonly bucketDurationSeconds: number
  readonly minimum: Int16Array
  readonly maximum: Int16Array
  readonly low: Uint16Array
  readonly mid: Uint16Array
  readonly high: Uint16Array
}

export interface DecodedTimeline {
  readonly durationSeconds: number
  readonly levels: readonly DecodedTimelineLevel[]
  readonly bpm: number
  readonly beatConfidence: number
  readonly rhythmCoverageSeconds: number
  readonly beats: Float32Array
  readonly localTempo: Float32Array
}
