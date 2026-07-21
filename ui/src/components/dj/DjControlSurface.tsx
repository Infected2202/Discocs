import { useEffect, useMemo, useRef, useState } from "react"
import { ChevronDown, Gauge, Pause, Play, Power, SkipForward, SlidersHorizontal, Sparkles } from "lucide-react"
import { useTranslation } from "react-i18next"
import { cn } from "@/lib/utils"
import {
  playerPlayback,
  type BeatSyncSnapshot,
  type DeckId,
  type DeckSnapshot,
  type EqBand,
} from "@/engine/playback"
import { usePlayerStore } from "@/store/playerStore"
import { useUIStore } from "@/store/uiStore"
import type { QueueItem as QueueItemType, TrackSummary } from "@/api/types"
import ArtworkImage from "@/components/media/ArtworkImage"
import QueueItem from "@/components/player/QueueItem"
import DjKnob from "./DjKnob"
import DjFader from "./DjFader"
import { usePlaybackEngineSnapshot } from "./usePlaybackEngineSnapshot"
import styles from "./DjControlSurface.module.css"
import { useTimeline } from "@/engine/timeline"
import type { TimelineLoadState } from "@/api/timeline"
import { WaveformSurface } from "@/engine/waveform"
import { resolveFollowWindow } from "@/engine/waveform/geometry"
import { useDeckPosition } from "./useDeckPosition"
import { useMixerMeter } from "./useMixerMeter"
import { useDroppable } from "@dnd-kit/core"
import type { DjDeckDropData } from "./DjTrackDragProvider"

const deckIds: DeckId[] = ["A", "B"]
const waveformWindowSteps = [8, 16, 30, 60] as const
const defaultWaveformWindow = 16

function roleLabel(role: DeckSnapshot["role"]): string {
  return role.toUpperCase()
}

function formatTime(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "--:--"
  const safe = Math.max(0, seconds)
  const minutes = Math.floor(safe / 60)
  return `${minutes}:${Math.floor(safe % 60).toString().padStart(2, "0")}`
}

function trackForDeck(
  deck: DeckSnapshot,
  queueItems: QueueItemType[],
  history: QueueItemType[],
  currentTrack: TrackSummary | null,
): TrackSummary | null {
  if (deck.queueItemId) {
    const item = [...queueItems, ...history].find((candidate) => candidate.id === deck.queueItemId)
    if (item?.track) return item.track
  }
  if (deck.trackId === currentTrack?.id) return currentTrack
  return null
}

const waveformPalette = { low: 0x2ed7ff, mid: 0xc738ff, high: 0xff6b3d, beat: 0xf8fafc, playhead: 0xff3b30 }

function DeckWaveform({ deck, track, state, windowSeconds }: {
  readonly deck: DeckSnapshot
  readonly track: TrackSummary | null
  readonly state: TimelineLoadState
  readonly windowSeconds: number
}) {
  const playhead = useDeckPosition(deck)
  const timeline = state.timeline
  let statusLabel = `Waveform · ${state.status}`
  if (state.status === "ready" && timeline) {
    statusLabel = timeline.beats.length > 0
      ? `${Math.round(timeline.bpm)} BPM · beat grid`
      : "Waveform ready · no beat grid"
  }
  const input = useMemo(() => timeline ? {
    timeline,
    viewport: {
      width: 1,
      height: 1,
      devicePixelRatio: globalThis.devicePixelRatio || 1,
      ...resolveFollowWindow(playhead, windowSeconds),
    },
    playheadSeconds: playhead,
    follow: true,
    palette: waveformPalette,
  } : null, [playhead, timeline, windowSeconds])
  return (
    <div className={styles.waveformRow} data-deck={deck.id}>
      <div className={styles.waveformDeckLabel}>{deck.id}</div>
      <div className={styles.waveformCanvas}>
        {input && <WaveformSurface
          className={styles.waveformRenderer}
          ariaLabel={`Deck ${deck.id} detailed waveform`}
          input={input}
          interaction="tape"
          onSeek={(seconds) => playerPlayback.seekDeckToSeconds(deck.id, seconds)}
        />}
        <div className={styles.waveformMessage}>
          <span>{track?.title ?? `Deck ${deck.id}`}</span>
          <small>{statusLabel}</small>
        </div>
      </div>
    </div>
  )
}

function OverviewWaveform({ deck, state }: { readonly deck: DeckSnapshot; readonly state: TimelineLoadState }) {
  const timeline = state.timeline
  const playhead = useDeckPosition(deck)
  const input = useMemo(() => timeline ? {
    timeline,
    viewport: {
      width: 1,
      height: 1,
      devicePixelRatio: globalThis.devicePixelRatio || 1,
      startSeconds: 0,
      endSeconds: timeline.durationSeconds,
    },
    playheadSeconds: playhead,
    follow: false,
    palette: waveformPalette,
  } : null, [playhead, timeline])
  if (!input) return <div className={styles.overviewWaveform}><span>{state.status}</span></div>
  return <div className={styles.overviewWaveform}><WaveformSurface
    className={styles.waveformRenderer}
    ariaLabel={`Deck ${deck.id} overview waveform`}
    input={input}
    onSeek={(seconds) => playerPlayback.seekDeckToSeconds(deck.id, seconds)}
  /></div>
}

function DeckTimeReadout({ deck }: { readonly deck: DeckSnapshot }) {
  const position = useDeckPosition(deck)
  return (
    <div className={styles.deckReadout}>
      <strong>-{formatTime(deck.duration ? deck.duration - position : null)}</strong>
      <span>{formatTime(position)} / {formatTime(deck.duration)}</span>
    </div>
  )
}

function EffectRack({ side }: { readonly side: DeckId }) {
  const heading = <div className={styles.sectionEyebrow}><Sparkles size={12} /> FX {side}</div>
  const controls = (
    <div className={styles.effectControls} data-part="effect-controls">
      <DjKnob label={`Deck ${side} effect mix`} displayLabel="D/W" value={0.5} defaultValue={0.5} disabled />
      <div className={styles.effectNames}>
        <span>Delay</span><span>Reverb</span><span>Flanger</span>
      </div>
      <DjKnob label={`Deck ${side} delay parameter`} displayLabel="DELAY" value={0.5} defaultValue={0.5} disabled />
      <DjKnob label={`Deck ${side} reverb parameter`} displayLabel="REVRB" value={0.5} defaultValue={0.5} disabled />
      <DjKnob label={`Deck ${side} flanger parameter`} displayLabel="FLANG" value={0.5} defaultValue={0.5} disabled />
    </div>
  )

  return (
    <section className={styles.effectRack} aria-label={`Deck ${side} effects`} data-side={side}>
      {side === "A" && heading}
      {controls}
      {side === "B" && heading}
      <span className={styles.unavailable}>Effects unavailable</span>
    </section>
  )
}

interface DeckPanelProps {
  readonly deck: DeckSnapshot
  readonly track: TrackSummary | null
  readonly isProgram: boolean
  readonly onToggle: () => void
  readonly onHandover: () => void
  readonly timelineState: TimelineLoadState
  readonly beatSync: BeatSyncSnapshot
}

function DeckPanel({ deck, track, isProgram, onToggle, onHandover, timelineState, beatSync }: DeckPanelProps) {
  const isPlaying = deck.transport === "playing"
  const canPlay = deck.trackId !== null && deck.preparation !== "loading" && deck.preparation !== "unavailable"
  const canHandover = !isProgram && deck.preparation === "ready"
  const dropData: DjDeckDropData = { kind: "dj-deck", deck: deck.id }
  const { isOver, setNodeRef } = useDroppable({
    id: `dj-deck-${deck.id}`,
    data: dropData,
    disabled: isProgram || isPlaying,
  })
  const pitchValue = Math.max(-1, Math.min(1, (deck.tempoRatio - 1) / 0.08))
  const pitchPercent = (deck.tempoRatio - 1) * 100
  const isTempoMaster = beatSync.master === deck.id
  const syncState = beatSync.decks[deck.id]

  return (
    <section
      ref={setNodeRef}
      className={cn(styles.deckPanel, isOver && styles.deckDropTarget)}
      aria-label={`Deck ${deck.id}`}
      data-deck={deck.id}
    >
      <header className={styles.deckHeader}>
        <ArtworkImage
          src={track?.artwork?.url}
          alt=""
          size={48}
          className={styles.deckArtwork}
          fallbackLetter={track?.title?.[0] ?? deck.id}
        />
        <div className={styles.deckMeta}>
          <strong>{track?.title ?? `Deck ${deck.id} empty`}</strong>
          <span>{track?.artists?.map((artist) => artist.name).join(", ") || "—"}</span>
          <div className={styles.deckSyncControls}>
            <button
              type="button"
              data-active={syncState.enabled || undefined}
              disabled={deck.trackId === null}
              onClick={() => runPlayerCommand(() => playerPlayback.toggleDeckSync(deck.id))}
              aria-label={`Sync Deck ${deck.id} to tempo master`}
            >SYNC</button>
            <button
              type="button"
              data-active={isTempoMaster || undefined}
              disabled={!isPlaying}
              onClick={() => runPlayerCommand(() => playerPlayback.setDeckTempoMaster(deck.id))}
              aria-label={`Set Deck ${deck.id} as tempo master`}
            >MASTER</button>
          </div>
        </div>
        <DeckTimeReadout deck={deck} />
        <div className={styles.deckIdentity}>
          <b>{deck.id}</b>
          <span className={cn(styles.roleBadge, isProgram && styles.roleProgram)}>{roleLabel(deck.role)}</span>
          <small>{deck.preparation} · {deck.transport} · {deck.tempoMode}</small>
        </div>
      </header>

      <OverviewWaveform deck={deck} state={timelineState} />

      <div className={styles.deckControls}>
        <div className={styles.transportControls}>
          <button
            type="button"
            className={cn(styles.playButton, isPlaying && styles.playing)}
            disabled={!canPlay}
            onClick={onToggle}
            aria-label={`${isPlaying ? "Pause" : "Play"} Deck ${deck.id}`}
          >
            {isPlaying ? <Pause size={17} fill="currentColor" /> : <Play size={17} fill="currentColor" />}
          </button>
          <button type="button" className={styles.secondaryButton} disabled>CUE</button>
          <button type="button" className={styles.secondaryButton} disabled>LOOP</button>
          <button
            type="button"
            className={styles.handoverButton}
            disabled={!canHandover}
            onClick={onHandover}
            aria-label={`Make Deck ${deck.id} program`}
          >
            <SkipForward size={14} /> PROGRAM
          </button>
        </div>
        <div className={styles.deckControlArea}>
          <div className={styles.beatControls}>
            {(["1/8", "1/4", "1/2", "1", "2", "4", "8", "16"]).map((beat) => (
              <button key={beat} type="button" disabled>{beat}</button>
            ))}
          </div>
          <div className={styles.hotcuePads}>
            {[1, 2, 3, 4, 5, 6, 7, 8].map((cue) => (
              <button key={cue} type="button" disabled>{cue}</button>
            ))}
          </div>
        </div>
      </div>
      <div className={styles.pitchControl}>
        <span>{pitchPercent >= 0 ? "+" : ""}{pitchPercent.toFixed(1)}%</span>
        <DjFader
          label={`Deck ${deck.id} pitch`}
          displayLabel="PITCH"
          value={pitchValue}
          min={-1}
          max={1}
          disabled={!canPlay || !isTempoMaster}
          onChange={(value) => runPlayerCommand(() => playerPlayback.setDeckTempo(deck.id, 1 + value * 0.08))}
        />
      </div>
    </section>
  )
}

function LevelMeter({ meter, label, className }: {
  readonly meter: DeckId | "master"
  readonly label: string
  readonly className?: string
}) {
  const value = useMixerMeter(meter)
  return (
    <meter className={cn(styles.meter, className)} aria-label={label} min={0} max={1} value={value}>
      <span style={{ height: `${Math.min(1, Math.max(0, value)) * 100}%` }} />
    </meter>
  )
}

interface MixerChannelProps {
  readonly deck: DeckId
  readonly mixer: ReturnType<typeof usePlaybackEngineSnapshot>["mixer"]
}

function MixerToggle({ label, active = false }: { readonly label: string; readonly active?: boolean }) {
  return (
    <button
      type="button"
      className={styles.controlToggle}
      data-active={active || undefined}
      disabled
      aria-label={label}
    />
  )
}

function MixerChannel({ deck, mixer }: MixerChannelProps) {
  return (
    <section className={styles.channel} aria-label={`Deck ${deck} mixer channel`} data-deck={deck}>
      <strong className={styles.channelLabel}>{deck}</strong>
      <div className={styles.channelBody}>
        <div className={styles.channelAux}>
          <DjKnob
            label={`Deck ${deck} gain`}
            displayLabel="GAIN"
            labelAccessory={<MixerToggle label={`Toggle Deck ${deck} gain`} />}
            value={mixer.trims[deck]}
            defaultValue={0.5}
            onChange={(value) => playerPlayback.setDeckTrim(deck, value)}
          />
          <DjKnob
            label={`Deck ${deck} filter`}
            labelContent={(
              <select className={styles.filterSelect} aria-label={`Deck ${deck} filter type`} defaultValue="filter" disabled>
                <option value="filter">Filter</option>
                <option value="reverb">Reverb</option>
                <option value="dual-delay">Dual Delay</option>
                <option value="noise">Noise</option>
                <option value="time-gater">Time Gater</option>
              </select>
            )}
            labelAccessory={<MixerToggle label={`Toggle Deck ${deck} filter`} active={mixer.filters[deck] !== 0} />}
            value={mixer.filters[deck]}
            min={-1}
            max={1}
            defaultValue={0}
            onChange={(value) => playerPlayback.setDeckFilter(deck, value)}
          />
          <fieldset className={styles.fxAssign} aria-label={`Deck ${deck} effect assignment`}>
            <div><button type="button" disabled>1</button><button type="button" disabled>2</button></div>
            <span>FX</span>
          </fieldset>
          <DjKnob
            label={`Deck ${deck} key`}
            displayLabel="KEY"
            labelAccessory={<MixerToggle label={`Match Deck ${deck} key`} />}
            value={0.5}
            defaultValue={0.5}
            disabled
          />
        </div>
        <LevelMeter
          meter={deck}
          label={`Deck ${deck} output level`}
          className={styles.channelMeter}
        />
        <div className={styles.channelEq}>
          {(["high", "mid", "low"] as EqBand[]).map((band) => (
            <DjKnob
              key={band}
              label={`Deck ${deck} ${band}`}
              displayLabel={band === "high" ? "HI" : band.toUpperCase()}
              labelAccessory={<MixerToggle label={`Mute Deck ${deck} ${band}`} />}
              value={mixer.eq[deck][band]}
              defaultValue={0.5}
              onChange={(value) => playerPlayback.setDeckEq(deck, band, value)}
            />
          ))}
          <div className={styles.channelOutput}>
            <DjFader
              label={`Deck ${deck} channel`}
              displayLabel=""
              value={mixer.channelFaders[deck]}
              onChange={(value) => playerPlayback.setDeckChannelFader(deck, value)}
            />
          </div>
        </div>
      </div>
    </section>
  )
}

function runPlayerCommand(command: () => Promise<void>): void {
  command().catch((error: Error) => {
    usePlayerStore.setState({ error: error.message })
  })
}

interface InactivePreviewProps {
  readonly track: TrackSummary | null
  readonly currentTime: number
  readonly duration: number
  readonly isPlaying: boolean
  readonly onActivate: () => void
}

/**
 * Панель, когда DJ-движок выключен: дека A зеркалит текущий трек и позицию из
 * обычного плеера (без звука через граф) плюс кнопка активации. Микшер и
 * управление графом не рендерим — граф ещё не инициализирован, а их обработчики
 * требуют живой AudioContext.
 */
function InactivePreview({ track, currentTime, duration, isPlaying, onActivate }: InactivePreviewProps) {
  const { t } = useTranslation("player")
  const progress = duration > 0 ? Math.min(1, Math.max(0, currentTime / duration)) : 0
  return (
    <section className={styles.inactivePreview} aria-label={t("djEngineInactiveTitle")}>
      <div className={styles.inactiveDeck}>
        <ArtworkImage
          src={track?.artwork?.url}
          alt=""
          size={72}
          className={styles.deckArtwork}
          fallbackLetter={track?.title?.[0] ?? "A"}
        />
        <div className={styles.inactiveMeta}>
          <span className={cn(styles.roleBadge, styles.roleProgram)}>A</span>
          <strong>{track?.title ?? t("nothingPlaying")}</strong>
          <span>{track?.artists?.map((artist) => artist.name).join(", ") || "—"}</span>
          <div className={styles.inactiveProgress} aria-hidden="true">
            <span style={{ width: `${progress * 100}%` }} />
          </div>
          <small>{formatTime(currentTime)} / {formatTime(duration)} · {isPlaying ? "play" : "pause"}</small>
        </div>
      </div>
      <div className={styles.inactiveCta}>
        <strong>{t("djEngineInactiveTitle")}</strong>
        <p>{t("djEngineInactiveHint")}</p>
        <button
          type="button"
          className={styles.engineActivate}
          data-testid="activate-dj-engine"
          onClick={onActivate}
        >
          <Power size={16} /> {t("activateDjEngine")}
        </button>
      </div>
    </section>
  )
}

function OpenDjControlSurface() {
  const { t } = useTranslation("player")
  const close = useUIStore((state) => state.closeDjSurface)
  const queue = usePlayerStore((state) => state.queue)
  const history = usePlayerStore((state) => state.playedHistory)
  const currentTrack = usePlayerStore((state) => state.currentTrack)
  const currentQueueItemId = usePlayerStore((state) => state.currentQueueItemId)
  const toggleDeck = usePlayerStore((state) => state.toggleDjDeck)
  const skipNext = usePlayerStore((state) => state.skipNext)
  const djEngineActive = usePlayerStore((state) => state.djEngineActive)
  const activateDj = usePlayerStore((state) => state.activateDj)
  const deactivateDj = usePlayerStore((state) => state.deactivateDj)
  const programCurrentTime = usePlayerStore((state) => state.currentTime)
  const programDuration = usePlayerStore((state) => state.duration)
  const programPlaybackState = usePlayerStore((state) => state.playbackState)
  const snapshot = usePlaybackEngineSnapshot()
  const timelineA = useTimeline(snapshot.decks.A.trackId)
  const timelineB = useTimeline(snapshot.decks.B.trackId)
  const timelines: Record<DeckId, TimelineLoadState> = { A: timelineA, B: timelineB }
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const restoreFocusRef = useRef<HTMLElement | null>(null)
  const [waveformWindowSeconds, setWaveformWindowSeconds] = useState<number>(defaultWaveformWindow)
  const queueItems = queue?.items ?? []
  const waveformWindowIndex = waveformWindowSteps.indexOf(
    waveformWindowSeconds as typeof waveformWindowSteps[number],
  )

  const tracks = useMemo(() => ({
    A: trackForDeck(snapshot.decks.A, queueItems, history, currentTrack),
    B: trackForDeck(snapshot.decks.B, queueItems, history, currentTrack),
  }), [snapshot.decks, queueItems, history, currentTrack])

  useEffect(() => {
    restoreFocusRef.current = document.activeElement as HTMLElement | null
    closeButtonRef.current?.focus()
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") close()
    }
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("keydown", onKeyDown)
      restoreFocusRef.current?.focus()
    }
  }, [close])

  return (
    <div
      className={cn(styles.surface, styles.open)}
      aria-hidden="false"
      data-testid="dj-control-surface"
    >
      <div className={styles.backdrop} />
      <div className={cn(styles.workspace, !djEngineActive && styles.workspaceInactive)}>
        <header className={styles.topPanel}>
          <EffectRack side="A" />
          <div className={styles.masterPanel}>
            <div className={styles.masterTitle}>
              <SlidersHorizontal size={15} /> DJ Workspace
              <button
                type="button"
                className={styles.engineToggle}
                data-active={djEngineActive || undefined}
                data-testid="toggle-dj-engine"
                onClick={() => runPlayerCommand(djEngineActive ? deactivateDj : activateDj)}
                aria-label={djEngineActive ? t("deactivateDjEngine") : t("activateDjEngine")}
              >
                <Power size={13} /> {djEngineActive ? t("deactivateDjEngine") : t("activateDjEngine")}
              </button>
            </div>
            <div className={styles.masterControls}>
              <div className={styles.masterReadout}>
                <span>MASTER BPM</span>
                <input
                  type="number"
                  min="1"
                  step="0.01"
                  value={snapshot.beatSync.clockBpm.toFixed(2)}
                  disabled={!djEngineActive || snapshot.beatSync.master !== "clock"}
                  onChange={(event) => runPlayerCommand(() => playerPlayback.setMasterClockTempo(Number(event.target.value)))}
                  aria-label="Master clock tempo"
                />
                <small>{snapshot.beatSync.master === "clock" ? "CLOCK" : `DECK ${snapshot.beatSync.master}`}</small>
                <div className={styles.masterClockModes}>
                  <button
                    type="button"
                    data-active={snapshot.beatSync.auto || undefined}
                    disabled={!djEngineActive}
                    onClick={() => runPlayerCommand(() => playerPlayback.setAutoTempoMaster())}
                    aria-label="Use automatic tempo master"
                  >AUTO</button>
                  <button
                    type="button"
                    data-active={snapshot.beatSync.master === "clock" || undefined}
                    disabled={!djEngineActive || deckIds.some((deck) => snapshot.decks[deck].transport === "playing")}
                    onClick={() => runPlayerCommand(() => playerPlayback.setClockTempoMaster())}
                    aria-label="Use master clock"
                  >MASTER</button>
                </div>
              </div>
              <DjKnob
                label="Master gain"
                displayLabel="MAIN"
                value={snapshot.mixer.masterGain}
                defaultValue={1}
                disabled={!djEngineActive}
                onChange={(value) => playerPlayback.setMasterGain(value)}
              />
              <LevelMeter meter="master" label="Master output level" />
            </div>
            <span className={styles.limiter}><Gauge size={12} /> protected</span>
          </div>
          <EffectRack side="B" />
          <button
            ref={closeButtonRef}
            type="button"
            className={styles.closeButton}
            data-testid="close-dj-surface"
            onClick={close}
            aria-label={t("closeDjSurface")}
          >
            <ChevronDown size={19} />
          </button>
        </header>

        {!djEngineActive && (
          <InactivePreview
            track={tracks.A ?? currentTrack}
            currentTime={programCurrentTime}
            duration={programDuration}
            isPlaying={programPlaybackState === "playing"}
            onActivate={() => runPlayerCommand(activateDj)}
          />
        )}

        {djEngineActive && <section className={styles.waveforms} aria-label="Deck waveforms">
          {deckIds.map((deck) => (
            <DeckWaveform
              key={deck}
              deck={snapshot.decks[deck]}
              track={tracks[deck]}
              state={timelines[deck]}
              windowSeconds={waveformWindowSeconds}
            />
          ))}
          {(timelineA.timeline || timelineB.timeline) && <div
            className={styles.waveformZoomControls}
            aria-label="Deck waveforms zoom"
          >
            <button
              type="button"
              aria-label="Zoom in deck waveforms"
              disabled={waveformWindowIndex === 0}
              onClick={() => setWaveformWindowSeconds(
                waveformWindowSteps[Math.max(0, waveformWindowIndex - 1)] ?? defaultWaveformWindow,
              )}
            >+</button>
            <button
              type="button"
              aria-label="Reset deck waveforms zoom"
              disabled={waveformWindowSeconds === defaultWaveformWindow}
              onClick={() => setWaveformWindowSeconds(defaultWaveformWindow)}
            >=</button>
            <button
              type="button"
              aria-label="Zoom out deck waveforms"
              disabled={waveformWindowIndex === waveformWindowSteps.length - 1}
              onClick={() => setWaveformWindowSeconds(
                waveformWindowSteps[Math.min(waveformWindowSteps.length - 1, waveformWindowIndex + 1)]
                  ?? defaultWaveformWindow,
              )}
            >−</button>
          </div>}
        </section>}

        {djEngineActive && <section className={styles.decksAndMixer}>
          <DeckPanel
            deck={snapshot.decks.A}
            track={tracks.A}
            isProgram={snapshot.programDeck === "A"}
            onToggle={() => runPlayerCommand(() => toggleDeck("A"))}
            onHandover={() => runPlayerCommand(skipNext)}
            timelineState={timelines.A}
            beatSync={snapshot.beatSync}
          />

          <section className={styles.mixerPanel} aria-label="Mixer">
            <div className={styles.mixerChannels}>
              {deckIds.map((deck) => (
                <MixerChannel key={deck} deck={deck} mixer={snapshot.mixer} />
              ))}
            </div>
            <DjFader
              label="Crossfader"
              displayLabel="CROSSFADER"
              value={snapshot.mixer.crossfader}
              min={-1}
              max={1}
              orientation="horizontal"
              onChange={(value) => playerPlayback.setCrossfader(value)}
            />
          </section>

          <DeckPanel
            deck={snapshot.decks.B}
            track={tracks.B}
            isProgram={snapshot.programDeck === "B"}
            onToggle={() => runPlayerCommand(() => toggleDeck("B"))}
            onHandover={() => runPlayerCommand(skipNext)}
            timelineState={timelines.B}
            beatSync={snapshot.beatSync}
          />
        </section>}

        <section className={styles.queuePanel} aria-label={t("queue")}>
          <header>
            <div>
              <span className={styles.sectionEyebrow}>{t("queue")}</span>
              <strong>{queue?.items.length ?? 0} tracks</strong>
            </div>
            <span>{queue?.current_item?.track?.title ?? currentTrack?.title ?? t("nothingPlaying")}</span>
          </header>
          <div className={styles.queueList}>
            {queueItems.length === 0 && <p className={styles.emptyQueue}>{t("queueEmpty")}</p>}
            {queueItems.map((item) => (
              <QueueItem
                key={item.id}
                track={item.track as TrackSummary | null}
                trackId={item.track_id}
                itemId={item.id}
                variant="queue"
                isCurrent={item.id === currentQueueItemId}
              />
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}

export default function DjControlSurface() {
  const open = useUIStore((state) => state.djSurfaceOpen)
  return open ? <OpenDjControlSurface /> : null
}
