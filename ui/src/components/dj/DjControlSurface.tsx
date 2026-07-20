import { useEffect, useMemo, useRef } from "react"
import { ChevronDown, Gauge, Pause, Play, SkipForward, SlidersHorizontal, Sparkles } from "lucide-react"
import { useTranslation } from "react-i18next"
import { cn } from "@/lib/utils"
import { playerPlayback, type DeckId, type DeckSnapshot, type EqBand } from "@/engine/playback"
import { usePlayerStore } from "@/store/playerStore"
import { useUIStore } from "@/store/uiStore"
import type { QueueItem as QueueItemType, TrackSummary } from "@/api/types"
import ArtworkImage from "@/components/media/ArtworkImage"
import QueueItem from "@/components/player/QueueItem"
import DjKnob from "./DjKnob"
import DjFader from "./DjFader"
import { usePlaybackEngineSnapshot } from "./usePlaybackEngineSnapshot"
import styles from "./DjControlSurface.module.css"

const deckIds: DeckId[] = ["A", "B"]

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
    if (item?.track) return item.track as TrackSummary
  }
  if (deck.trackId === currentTrack?.id) return currentTrack
  return null
}

function WaveformPlaceholder({ deck, track }: { readonly deck: DeckSnapshot; readonly track: TrackSummary | null }) {
  return (
    <div className={styles.waveformRow} data-deck={deck.id}>
      <div className={styles.waveformDeckLabel}>{deck.id}</div>
      <div className={styles.waveformCanvas}>
        <div className={styles.waveformPattern} />
        <div className={styles.playhead} />
        <div className={styles.waveformMessage}>
          <span>{track?.title ?? `Deck ${deck.id}`}</span>
          <small>Waveform · Phase 4</small>
        </div>
      </div>
    </div>
  )
}

function EffectRack({ side }: { readonly side: DeckId }) {
  return (
    <section className={styles.effectRack} aria-label={`Deck ${side} effects`} aria-disabled="true">
      <div className={styles.sectionEyebrow}><Sparkles size={12} /> FX {side}</div>
      <div className={styles.effectControls}>
        <DjKnob label={`Deck ${side} effect mix`} value={0.5} defaultValue={0.5} disabled />
        <div className={styles.effectNames}>
          <span>Delay</span><span>Reverb</span><span>Flanger</span>
        </div>
        <DjKnob label={`Deck ${side} effect parameter`} value={0.5} defaultValue={0.5} disabled />
      </div>
      <span className={styles.unavailable}>Effects unavailable</span>
    </section>
  )
}

interface DeckPanelProps {
  readonly deck: DeckSnapshot
  readonly track: TrackSummary | null
  readonly isProgram: boolean
  readonly mixer: ReturnType<typeof usePlaybackEngineSnapshot>["mixer"]
  readonly onToggle: () => void
  readonly onHandover: () => void
}

function DeckPanel({ deck, track, isProgram, mixer, onToggle, onHandover }: DeckPanelProps) {
  const isPlaying = deck.transport === "playing"
  const canPlay = deck.trackId !== null && deck.preparation !== "loading" && deck.preparation !== "unavailable"
  const canHandover = !isProgram && deck.preparation === "ready"

  return (
    <section className={styles.deckPanel} aria-label={`Deck ${deck.id}`}>
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
        </div>
        <div className={styles.deckReadout}>
          <strong>-{formatTime(deck.duration && deck.anchor ? deck.duration - deck.anchor.mediaSeconds : null)}</strong>
          <span>{formatTime(deck.duration)}</span>
        </div>
        <div className={styles.deckIdentity}>
          <b>{deck.id}</b>
          <span className={cn(styles.roleBadge, isProgram && styles.roleProgram)}>{roleLabel(deck.role)}</span>
          <small>{deck.preparation} · {deck.transport}</small>
        </div>
      </header>

      <div className={styles.overviewWaveform}>
        <div className={styles.overviewPattern} />
        <span>overview pending</span>
      </div>

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
        <div className={styles.deckMixerStrip}>
          <DjKnob
            label={`Deck ${deck.id} gain`}
            value={mixer.trims[deck.id]}
            defaultValue={0.5}
            onChange={(value) => playerPlayback.setDeckTrim(deck.id, value)}
          />
          {(["high", "mid", "low"] as EqBand[]).map((band) => (
            <DjKnob
              key={band}
              label={`Deck ${deck.id} ${band}`}
              value={mixer.eq[deck.id][band]}
              defaultValue={0.8}
              onChange={(value) => playerPlayback.setDeckEq(deck.id, band, value)}
            />
          ))}
          <DjKnob
            label={`Deck ${deck.id} filter`}
            value={mixer.filters[deck.id]}
            min={-1}
            max={1}
            defaultValue={0}
            onChange={(value) => playerPlayback.setDeckFilter(deck.id, value)}
          />
        </div>
      </div>
    </section>
  )
}

function LevelMeter({ value, label }: { readonly value: number; readonly label: string }) {
  return (
    <div className={styles.meter} aria-label={label} role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={value}>
      <span style={{ height: `${Math.min(1, Math.max(0, value)) * 100}%` }} />
    </div>
  )
}

export default function DjControlSurface() {
  const { t } = useTranslation("player")
  const open = useUIStore((state) => state.djSurfaceOpen)
  const close = useUIStore((state) => state.closeDjSurface)
  const queue = usePlayerStore((state) => state.queue)
  const history = usePlayerStore((state) => state.playedHistory)
  const currentTrack = usePlayerStore((state) => state.currentTrack)
  const currentQueueItemId = usePlayerStore((state) => state.currentQueueItemId)
  const toggleDeck = usePlayerStore((state) => state.toggleDjDeck)
  const skipNext = usePlayerStore((state) => state.skipNext)
  const snapshot = usePlaybackEngineSnapshot()
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const restoreFocusRef = useRef<HTMLElement | null>(null)
  const queueItems = queue?.items ?? []

  const tracks = useMemo(() => ({
    A: trackForDeck(snapshot.decks.A, queueItems, history, currentTrack),
    B: trackForDeck(snapshot.decks.B, queueItems, history, currentTrack),
  }), [snapshot.decks, queueItems, history, currentTrack])

  useEffect(() => {
    if (!open) return
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
  }, [close, open])

  return (
    <div
      className={cn(styles.surface, open ? styles.open : styles.closed)}
      aria-hidden={!open}
      data-testid="dj-control-surface"
    >
      <div className={styles.backdrop} />
      <div className={styles.workspace}>
        <header className={styles.topPanel}>
          <EffectRack side="A" />
          <div className={styles.masterPanel}>
            <div className={styles.masterTitle}><SlidersHorizontal size={15} /> DJ Workspace</div>
            <div className={styles.masterReadout}>
              <span>MASTER BPM</span>
              <strong>--.--</strong>
              <small>Timeline pending</small>
            </div>
            <DjKnob
              label="Master gain"
              value={snapshot.mixer.masterGain}
              defaultValue={1}
              onChange={(value) => playerPlayback.setMasterGain(value)}
            />
            <LevelMeter value={snapshot.mixer.meters.master} label="Master output level" />
            <span className={styles.limiter}><Gauge size={12} /> protected</span>
          </div>
          <EffectRack side="B" />
          <button
            ref={closeButtonRef}
            type="button"
            className={styles.closeButton}
            onClick={close}
            aria-label={t("closeDjSurface")}
          >
            <ChevronDown size={19} />
          </button>
        </header>

        <section className={styles.waveforms} aria-label="Deck waveforms">
          {deckIds.map((deck) => (
            <WaveformPlaceholder key={deck} deck={snapshot.decks[deck]} track={tracks[deck]} />
          ))}
        </section>

        <section className={styles.decksAndMixer}>
          <DeckPanel
            deck={snapshot.decks.A}
            track={tracks.A}
            isProgram={snapshot.programDeck === "A"}
            mixer={snapshot.mixer}
            onToggle={() => void toggleDeck("A")}
            onHandover={() => void skipNext()}
          />

          <section className={styles.mixerPanel} aria-label="Mixer">
            <div className={styles.mixerChannels}>
              {deckIds.map((deck) => (
                <div key={deck} className={styles.channel}>
                  <span>{deck}</span>
                  <LevelMeter value={snapshot.mixer.meters[deck]} label={`Deck ${deck} output level`} />
                  <DjFader
                    label={`Deck ${deck} channel`}
                    value={snapshot.mixer.channelFaders[deck]}
                    onChange={(value) => playerPlayback.setDeckChannelFader(deck, value)}
                  />
                </div>
              ))}
            </div>
            <DjFader
              label="Crossfader"
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
            mixer={snapshot.mixer}
            onToggle={() => void toggleDeck("B")}
            onHandover={() => void skipNext()}
          />
        </section>

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
