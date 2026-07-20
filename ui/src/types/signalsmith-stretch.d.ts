declare module "signalsmith-stretch" {
  export interface SignalsmithStretchFactory {
    (context: AudioContext, options?: AudioWorkletNodeOptions): Promise<import("../engine/playback/signalsmith/types").StretchNode>
    moduleUrl?: string
  }

  const createSignalsmithStretch: SignalsmithStretchFactory
  export default createSignalsmithStretch
}

declare module "signalsmith-stretch?url" {
  const url: string
  export default url
}
