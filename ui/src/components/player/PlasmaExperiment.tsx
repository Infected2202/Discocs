import { useEffect, useRef } from "react"
import { Mesh, Program, Renderer, Triangle } from "ogl"
import {
  easeTrackAccentTransition,
  mixPlasmaColor,
  parsePlasmaColor,
  plasmaShaderInitializers,
  readTrackAccentTransitionDurationMs,
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

// Experiment: symmetric turbulence instead of rising smoke.
//
// Original behaviour came from:
//   d = p.y - T          → field drifts upward as T grows
//   (1. + p.y) * sin()   → deformation grows stronger near top
//
// Here:
//   d = p.y              → no directional drift, field is static geometry
//   T injected into sin  → motion comes from oscillating warp, not translation
//   added p.y deform     → symmetric with p.x → turbulence in all directions
const fragmentExperiment = `#version 300 es
precision highp float;
uniform vec2 iResolution;
uniform float iTime;
uniform vec3 uCustomColor;
uniform float uSpeed;
uniform float uScale;
uniform float uOpacity;
out vec4 fragColor;

void mainImage(out vec4 o, vec2 C) {
  vec2 center = iResolution.xy * 0.5;
  C = (C - center) / uScale + center;

  ${plasmaShaderInitializers}

  for (vec2 r = iResolution.xy, Q; ++i < 60.; O += o.w / d * o.xyz) {
    p = z * normalize(vec3(C - .5 * r, r.y));
    p.z -= 4.;
    S = p;

    // static slab — no upward drift
    d = p.y;

    // symmetric deformation in both X and Y, oscillating with T
    p.x += .4 * sin(d + p.x * .1 + T * 0.8) * cos(.34 * d + p.x * .05 - T * 0.3);
    p.y += .4 * cos(d + p.y * .1 - T * 0.8) * sin(.34 * d + p.y * .05 + T * 0.3);

    Q = p.xz *= mat2(cos(p.y + vec4(0, 11, 33, 0) - T));
    z += d = abs(sqrt(length(Q * Q)) - .25 * (5. + S.y)) / 3. + 8e-4;
    o = 1. + sin(S.y + p.z * .5 + S.z - length(S - p) + vec4(2, 1, 0, 8));
  }

  o.xyz = tanh(O / 1e4);
}

bool finite1(float x) {
  return !(isnan(x) || isinf(x));
}

vec3 sanitize(vec3 c) {
  return vec3(
    finite1(c.r) ? c.r : 0.0,
    finite1(c.g) ? c.g : 0.0,
    finite1(c.b) ? c.b : 0.0
  );
}

void main() {
  vec4 o = vec4(0.0);
  mainImage(o, gl_FragCoord.xy);
  vec3 rgb = sanitize(o.rgb);
  float intensity = (rgb.r + rgb.g + rgb.b) / 3.0;
  vec3 finalColor = intensity * uCustomColor;
  float alpha = length(rgb) * uOpacity;
  fragColor = vec4(finalColor, alpha);
}`

interface PlasmaExperimentProps {
  active: boolean
  accent: string
  speed?: number
  scale?: number
  opacity?: number
}

export default function PlasmaExperiment({
  active,
  accent,
  speed = 0.2,
  scale = 1.7,
  opacity = 0.5,
}: PlasmaExperimentProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const activeRef = useRef(active)
  const accentRef = useRef(accent)
  const speedRef = useRef(speed)
  const scaleRef = useRef(scale)
  const opacityRef = useRef(opacity)
  const programRef = useRef<Program | null>(null)
  const currentColorRef = useRef<PlasmaRgb>(parsePlasmaColor(accent))
  const transitionRef = useRef<{
    from: PlasmaRgb
    to: PlasmaRgb
    startTime: number
    durationMs: number
  } | null>(null)

  useEffect(() => {
    activeRef.current = active
  }, [active])

  useEffect(() => {
    speedRef.current = speed
    scaleRef.current = scale
    opacityRef.current = opacity

    const program = programRef.current
    if (!program) return
    program.uniforms.uSpeed.value = speed * 0.4
    program.uniforms.uScale.value = scale
    program.uniforms.uOpacity.value = opacity
  }, [opacity, scale, speed])

  useEffect(() => {
    accentRef.current = accent

    const nextColor = parsePlasmaColor(accent)
    const program = programRef.current
    if (!program) {
      currentColorRef.current = nextColor
      return
    }

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)")
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
    const container = containerRef.current
    if (!container) return

    let renderer: Renderer
    try {
      renderer = new Renderer({
        webgl: 2,
        alpha: true,
        antialias: false,
        dpr: Math.min(window.devicePixelRatio || 1, 1.25),
      })
    } catch {
      return
    }

    const gl = renderer.gl
    const canvas = gl.canvas as HTMLCanvasElement
    canvas.style.display = "block"
    canvas.style.width = "100%"
    canvas.style.height = "100%"
    container.appendChild(canvas)

    const geometry = new Triangle(gl)
    const initialColor = parsePlasmaColor(accentRef.current)
    currentColorRef.current = initialColor

    const program = new Program(gl, {
      vertex,
      fragment: fragmentExperiment,
      uniforms: {
        iTime: { value: 0 },
        iResolution: { value: new Float32Array([1, 1]) },
        uCustomColor: { value: new Float32Array(initialColor) },
        uSpeed: { value: speedRef.current * 0.4 },
        uScale: { value: scaleRef.current },
        uOpacity: { value: opacityRef.current },
      },
    })
    programRef.current = program
    const mesh = new Mesh(gl, { geometry, program })

    const renderFrame = () => {
      renderer.render({ scene: mesh })
    }

    const setSize = () => {
      const rect = container.getBoundingClientRect()
      renderer.setSize(
        Math.max(1, Math.floor(rect.width)),
        Math.max(1, Math.floor(rect.height))
      )
      const resolution = program.uniforms.iResolution.value as Float32Array
      resolution[0] = gl.drawingBufferWidth
      resolution[1] = gl.drawingBufferHeight
    }
    const resizeObserver = new ResizeObserver(setSize)
    resizeObserver.observe(container)
    setSize()

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)")
    let elapsed = 0
    let previousTime = performance.now()
    let animationFrame = 0

    const render = (time: number) => {
      const delta = Math.min(100, time - previousTime)
      previousTime = time
      let shouldRender = false

      const transition = transitionRef.current
      if (transition) {
        const progress = transition.durationMs <= 0
          ? 1
          : Math.min(1, (time - transition.startTime) / transition.durationMs)
        const eased = easeTrackAccentTransition(progress)
        const color = mixPlasmaColor(transition.from, transition.to, eased)
        currentColorRef.current = color
        ;(program.uniforms.uCustomColor.value as Float32Array).set(color)
        shouldRender = true

        if (progress >= 1) {
          transitionRef.current = null
        }
      }

      if (activeRef.current && !reducedMotion.matches) {
        elapsed += delta * 0.001
        program.uniforms.iTime.value = elapsed
        shouldRender = true
      }

      if (shouldRender) {
        renderFrame()
      }

      animationFrame = requestAnimationFrame(render)
    }

    renderFrame()
    animationFrame = requestAnimationFrame(render)

    return () => {
      programRef.current = null
      transitionRef.current = null
      cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      canvas.remove()
    }
  }, [])

  return <div ref={containerRef} className="absolute inset-0 overflow-hidden" />
}
