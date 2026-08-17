interface VerticalDrag {
  offsetY: number;
  velocityY: number;
  viewportHeight: number;
}

/**
 * A distance threshold prevents a form scroll or tap becoming a dismissal;
 * a fast downward flick remains available for one-handed use.
 */
export function shouldDismissVerticalDrag({
  offsetY,
  velocityY,
  viewportHeight,
}: VerticalDrag): boolean {
  if (offsetY <= 0) return false;
  const distanceThreshold = Math.min(
    120,
    Math.max(72, viewportHeight * 0.12),
  );
  return (
    offsetY >= distanceThreshold ||
    (offsetY >= 20 && velocityY >= 800)
  );
}
