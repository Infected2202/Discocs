import { useEffect, useRef, useState } from "react"
import { Mesh, Program, Renderer, Triangle } from "ogl"
import {
  easeTrackAccentTransition,
  mixPlasmaColor,
  parsePlasmaColor,
  readTrackAccentTransitionDurationMs,
  shouldAdvancePlasmaFrame,
  type PlasmaRgb,
} from "./plasmaUtils.ts"

const vertex = `#version 300 es
precision highp float;
in vec2 position;
in vec2 uv;
out vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position, 0.0, 1.0);
}`

// FBM (Fractional Brownian Motion) with domain warping.
//
// No objects, no ray marching — just a noise field at every pixel.
// Domain warping (Inigo Quilez technique): warp the sampling coordinates
// with another FBM layer before the final sample → organic fluid turbulence.
const fragment = `#version 300 es
precision highp float;
uniform vec2 iResolution;
uniform float iTime;
uniform vec3 uCustomColor;
uniform float uSpeed;
uniform float uScale;
uniform float uOpacity;
out vec4 fragColor;

// Gradient noise hash — returns unit-ish vector per grid cell
vec2 hash2(vec2 p) {
  p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
  return -1.0 + 2.0 * fract(sin(p) * 43758.5453);
}

// Gradient noise in [-1, 1]
float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(dot(hash2(i + vec2(0,0)), f - vec2(0,0)),
        dot(hash2(i + vec2(1,0)), f - vec2(1,0)), u.x),
    mix(dot(hash2(i + vec2(0,1)), f - vec2(0,1)),
        dot(hash2(i + vec2(1,1)), f - vec2(1,1)), u.x),
    u.y
  );
}

// Rotation+scale matrix — breaks axis-aligned artifacts between octaves
const mat2 ROT = mat2(1.6, 1.2, -1.2, 1.6);

// 6-octave FBM
float fbm(vec2 p) {
  float v = 0.0, amp = 0.5;
  for (int i = 0; i < 6; i++) {
    v += amp * noise(p);
    p = ROT * p;
    amp *= 0.5;
  }
  return v;
}

void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * iResolution.xy) / iResolution.y / uScale;
  float t = iTime * uSpeed;

  // Slow rotation — field churns in place, no directional drift
  float angle = t * 0.06;
  mat2 rot = mat2(cos(angle), -sin(angle), sin(angle), cos(angle));

  // Small oscillating nudge so internal detail also evolves
  vec2 drift = vec2(sin(t * 0.11) * 0.2, cos(t * 0.07) * 0.2);

  vec2 rp = rot * p;

  // First warp layer
  vec2 q = vec2(
    fbm(rp + drift),
    fbm(rp + vec2(5.20, 1.30) - drift)
  );

  // Second warp layer
  vec2 r = vec2(
    fbm(rp + 2.0 * q + vec2(1.70, 9.20)),
    fbm(rp + 2.0 * q + vec2(8.30, 2.80))
  );

  float n = fbm(p + 4.5 * r);
  n = n * 0.5 + 0.5;
  n = smoothstep(0.25, 0.97, n);

  fragColor = vec4(uCustomColor * n, n * uOpacity);
}`

interface PlasmaFBMProps {
  readonly active: boolean
  readonly accent: string
  readonly speed?: number
  readonly scale?: number
  readonly opacity?: number
}

export default function PlasmaFBM({
  active,
  accent,
  speed = 0.3,
  scale = 1.0,
  opacity = 0.7,
}: PlasmaFBMProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const activeRef = useRef(active)
  const programRef = useRef<Program | null>(null)
  const [pageVisible, setPageVisible] = useState(() => typeof document === "undefined" || !document.hidden)
  const currentColorRef = useRef<PlasmaRgb>(parsePlasmaColor(accent))
  const transitionRef = useRef<{
    from: PlasmaRgb; to: PlasmaRgb; startTime: number; durationMs: number
  } | null>(null)

  useEffect(() => { activeRef.current = active }, [active])

  // A stopped animation frame does not release the canvas' GPU allocation.
  // On mobile that allocation makes this otherwise idle tab a more likely
  // discard candidate, so unmount the renderer while the document is hidden.
  useEffect(() => {
    const onVisibilityChange = () => setPageVisible(!document.hidden)
    document.addEventListener("visibilitychange", onVisibilityChange)
    return () => document.removeEventListener("visibilitychange", onVisibilityChange)
  }, [])

  useEffect(() => {
    const program = programRef.current
    if (!program) return
    program.uniforms.uSpeed.value = speed * 0.4
    program.uniforms.uScale.value = scale
    program.uniforms.uOpacity.value = opacity
  }, [speed, scale, opacity])

  useEffect(() => {
    const nextColor = parsePlasmaColor(accent)
    const program = programRef.current
    if (!program) { currentColorRef.current = nextColor; return }

    const reducedMotion = globalThis.matchMedia("(prefers-reduced-motion: reduce)")
    if (reducedMotion.matches) {
      currentColorRef.current = nextColor
      transitionRef.current = null
      ;(program.uniforms.uCustomColor.value as Float32Array).set(nextColor)
      return
    }
    transitionRef.current = {
      from: currentColorRef.current,
      to: nextColor,
      startTime: performance.now(),
      durationMs: readTrackAccentTransitionDurationMs(),
    }
  }, [accent])

  useEffect(() => {
    if (!pageVisible) return
    const container = containerRef.current
    if (!container) return

    const coarsePointer = globalThis.matchMedia?.("(pointer: coarse)").matches ?? false
    let renderer: Renderer
    try {
      renderer = new Renderer({
        webgl: 2,
        alpha: true,
        antialias: false,
        dpr: coarsePointer ? 0.75 : Math.min(devicePixelRatio, 1.25),
      })
    } catch { return }

    const gl = renderer.gl
    const canvas = gl.canvas as HTMLCanvasElement
    canvas.style.cssText = "display:block;width:100%;height:100%"
    container.appendChild(canvas)

    const initialColor = parsePlasmaColor(accent)
    currentColorRef.current = initialColor

    const program = new Program(gl, {
      vertex, fragment,
      uniforms: {
        iTime:        { value: 0 },
        iResolution:  { value: new Float32Array([1, 1]) },
        uCustomColor: { value: new Float32Array(initialColor) },
        uSpeed:       { value: speed * 0.4 },
        uScale:       { value: scale },
        uOpacity:     { value: opacity },
      },
    })
    programRef.current = program
    const mesh = new Mesh(gl, { geometry: new Triangle(gl), program })

    const setSize = () => {
      const rect = container.getBoundingClientRect()
      renderer.setSize(Math.max(1, Math.floor(rect.width)), Math.max(1, Math.floor(rect.height)))
      const res = program.uniforms.iResolution.value as Float32Array
      res[0] = gl.drawingBufferWidth
      res[1] = gl.drawingBufferHeight
    }
    const ro = new ResizeObserver(setSize)
    ro.observe(container)
    setSize()

    const reducedMotion = globalThis.matchMedia("(prefers-reduced-motion: reduce)")
    let elapsed = 0, lastFrameTime = performance.now(), raf = 0

    const render = (time: number) => {
      raf = requestAnimationFrame(render)

      // Кап ~30 fps: медленный фон не нуждается в 60 fps, работа вдвое меньше.
      if (!shouldAdvancePlasmaFrame(time, lastFrameTime)) return
      // Full-screen six-octave FBM is particularly expensive on phone GPUs.
      // Fifteen frames per second preserves slow ambient motion without
      // competing with scrolling, player controls and waveform rendering.
      if (coarsePointer && time - lastFrameTime < 1000 / 15) return
      const delta = Math.min(100, time - lastFrameTime); lastFrameTime = time
      let dirty = false

      const tr = transitionRef.current
      if (tr) {
        const progress = tr.durationMs <= 0 ? 1 : Math.min(1, (time - tr.startTime) / tr.durationMs)
        const color = mixPlasmaColor(tr.from, tr.to, easeTrackAccentTransition(progress))
        currentColorRef.current = color
        ;(program.uniforms.uCustomColor.value as Float32Array).set(color)
        dirty = true
        if (progress >= 1) transitionRef.current = null
      }

      if (activeRef.current && !reducedMotion.matches) {
        elapsed += delta * 0.001
        program.uniforms.iTime.value = elapsed
        dirty = true
      }

      if (dirty) renderer.render({ scene: mesh })
    }

    const startLoop = () => {
      if (raf) return
      lastFrameTime = performance.now()
      raf = requestAnimationFrame(render)
    }
    const stopLoop = () => {
      cancelAnimationFrame(raf)
      raf = 0
    }

    renderer.render({ scene: mesh })
    startLoop()

    return () => {
      programRef.current = null
      transitionRef.current = null
      stopLoop()
      ro.disconnect()
      // OGL does not automatically lose a context when its canvas leaves the
      // DOM. Ask the browser to release GPU memory; a fresh renderer is built
      // when the tab becomes visible again.
      gl.getExtension("WEBGL_lose_context")?.loseContext()
      canvas.remove()
    }
  }, [pageVisible]) // Props are kept current by the small effects above.

  return <div ref={containerRef} className="absolute inset-0 overflow-hidden" />
}
