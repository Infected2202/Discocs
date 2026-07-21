import { usePlayerStore } from "./playerStore"
import { useUIStore } from "./uiStore"

/**
 * Открыть DJ-панель, не активируя движок. Это чисто презентационное действие:
 * панель показывает деку с текущим треком (позиция зеркалится), а звук идёт
 * через граф только после явной активации кнопкой внутри панели (activateDj).
 * Панель и движок — две независимые оси: свернуть панель можно, не выключая
 * сведение.
 */
export function openDjPresentation(): void {
  usePlayerStore.setState({ expanded: false })
  useUIStore.getState().openDjSurface()
}
