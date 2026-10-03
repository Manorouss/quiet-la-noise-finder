export function pilotMapPadding(host, panel, gutter = 24) {
  const defaults = { top: gutter, right: gutter, bottom: gutter, left: gutter };
  if (!host || !panel) return defaults;
  const left = Math.max(host.left, panel.left);
  const right = Math.min(host.right, panel.right);
  const top = Math.max(host.top, panel.top);
  const bottom = Math.min(host.bottom, panel.bottom);
  const overlapWidth = Math.max(0, right - left);
  const overlapHeight = Math.max(0, bottom - top);
  if (!overlapWidth || !overlapHeight) return defaults;
  const centerX = (left + right) / 2;
  const centerY = (top + bottom) / 2;
  if (overlapWidth >= host.width * 0.65 || panel.width >= host.width * 0.65) {
    if (centerY >= (host.top + host.bottom) / 2) defaults.bottom = Math.ceil(Math.min(host.height - 80, host.bottom - panel.top + gutter));
    else defaults.top = Math.ceil(Math.min(host.height - 80, panel.bottom - host.top + gutter));
  } else if (centerX >= (host.left + host.right) / 2) defaults.right = Math.ceil(Math.min(host.width - 80, host.right - panel.left + gutter));
  else defaults.left = Math.ceil(Math.min(host.width - 80, panel.right - host.left + gutter));
  return defaults;
}

export function isPointInPaddedMapViewport(point, size, padding) {
  return point.x >= padding.left && point.x <= size.width - padding.right
    && point.y >= padding.top && point.y <= size.height - padding.bottom;
}
