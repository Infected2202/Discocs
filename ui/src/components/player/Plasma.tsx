import { useEffect, useRef } from "react"
import { Mesh, Program, Renderer, Triangle } from "ogl"
import { playerLog } from "@/lib/playerLogger"
import {
  easeTrackAccentTransition,
  mixPlasmaColor,
  parsePlasmaColor,
  plasmaShaderInitializers,
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

const fragment = `#version 300 es
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
    d = p.y - T;

    p.x += .4 * (1. + p.y) * sin(d + p.x * .1) * cos(.34 * d + p.x * .05);
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

interface PlasmaProps {
  active: boolean
  accent: string
  speed?: number
  scale?: number
  opacity?: number
}

export default function Plasma({
  active,
  accent,
  speed = 0.2,
  scale = 1.7,
  opacity = 0.5,
}: PlasmaProps) {
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
      playerLog("plasma", "sync color", {
        color: nextColor,
        artworkUrl: document.documentElement.dataset.trackAccentArtwork ?? "",
        immediate: true,
      })
      return
    }

    transitionRef.current = {
      from: currentColorRef.current,
      to: nextColor,
      startTime: performance.now(),
      durationMs: readTrackAccentTransitionDurationMs(),
    }
    playerLog("plasma", "transition color", {
      from: currentColorRef.current,
      to: nextColor,
      durationMs: transitionRef.current.durationMs,
      artworkUrl: document.documentElement.dataset.trackAccentArtwork ?? "",
    })
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
    playerLog("plasma", "mount", {
      active: activeRef.current,
      accent: accentRef.current,
      speed: speedRef.current,
      scale: scaleRef.current,
      opacity: opacityRef.current,
      initialColor,
      artworkUrl: document.documentElement.dataset.trackAccentArtwork ?? "",
    })
    const program = new Program(gl, {
      vertex,
      fragment,
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
    playerLog("plasma", "size", {
      width: gl.drawingBufferWidth,
      height: gl.drawingBufferHeight,
    })

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)")
    let elapsed = 0
    let lastFrameTime = performance.now()
    let animationFrame = 0

    const render = (time: number) => {
      animationFrame = requestAnimationFrame(render)

      // Кап ~30 fps: пропускаем кадры, пришедшие раньше интервала. delta
      // считаем от прошлого отрисованного кадра, чтобы скорость анимации не
      // зависела от частоты.
      if (!shouldAdvancePlasmaFrame(time, lastFrameTime)) return
      const delta = Math.min(100, time - lastFrameTime)
      lastFrameTime = time
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
          playerLog("plasma", "sync color", {
            color,
            artworkUrl: document.documentElement.dataset.trackAccentArtwork ?? "",
            immediate: false,
          })
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
    }

    const startLoop = () => {
      if (animationFrame) return
      // Сбрасываем точку отсчёта, иначе после долгой скрытости первый delta
      // будет огромным и анимация дёрнется.
      lastFrameTime = performance.now()
      animationFrame = requestAnimationFrame(render)
    }

    const stopLoop = () => {
      cancelAnimationFrame(animationFrame)
      animationFrame = 0
    }

    // Пауза, когда вкладка скрыта: на неё никто не смотрит — отпускаем GPU.
    const onVisibilityChange = () => {
      if (document.hidden) stopLoop()
      else startLoop()
    }
    document.addEventListener("visibilitychange", onVisibilityChange)

    renderFrame()
    if (!document.hidden) startLoop()

    return () => {
      playerLog("plasma", "unmount")
      programRef.current = null
      transitionRef.current = null
      document.removeEventListener("visibilitychange", onVisibilityChange)
      stopLoop()
      resizeObserver.disconnect()
      canvas.remove()
    }
  }, [])

  return <div ref={containerRef} className="absolute inset-0 overflow-hidden" />
}
