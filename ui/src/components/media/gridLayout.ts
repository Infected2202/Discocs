// Сколько колонок помещается в контейнер шириной containerWidth при раскладке
// CSS `repeat(auto-fill, minmax(minColumnWidth, 1fr))` с зазором gap.
// Повторяет логику auto-fill: число дорожек = floor((W + gap) / (min + gap)),
// потому что у N колонок ровно N-1 зазоров.
export function computeGridColumns(
  containerWidth: number,
  minColumnWidth: number,
  gap: number
): number {
  if (containerWidth <= 0 || minColumnWidth <= 0) return 1
  const columns = Math.floor((containerWidth + gap) / (minColumnWidth + gap))
  return Math.max(1, columns)
}
