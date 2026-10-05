// Map symbols drawn at runtime (no sprite files): the map asks for them through 'styleimagemissing'.
// Drawn at 2x for sharp icons on high-density screens; add with { pixelRatio: 2 }.
export type IconImage = { width: number; height: number; data: Uint8ClampedArray };

export const HELIPAD_BLUE = '#2b5fa8';
export const HOSPITAL_RED = '#c0262d';
export const FIRE_RED = '#c53030';

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Fire station: a firehouse (pitched roof) with a big engine door and two door panels, centred on (cx, cy),
 * `s` units tall, as an SVG path with even-odd holes (shared by the map icon and the panel). */
export function stationPath(cx: number, cy: number, s: number) {
  const k = s / 24;
  const p = (x: number, y: number) => `${+(cx + x * k).toFixed(2)} ${+(cy + y * k).toFixed(2)}`;
  const rect = (x0: number, y0: number, x1: number, y1: number) => `M${p(x0, y0)}L${p(x1, y0)}L${p(x1, y1)}L${p(x0, y1)}Z`;
  return `M${p(0, -11.5)}L${p(11.5, -1.5)}L${p(8.5, -1.5)}L${p(8.5, 11)}L${p(-8.5, 11)}L${p(-8.5, -1.5)}L${p(-11.5, -1.5)}Z`
    + `M${p(-5, 11)}L${p(-5, 3.2)}Q${p(-5, 1.4)} ${p(-3.2, 1.4)}L${p(3.2, 1.4)}Q${p(5, 1.4)} ${p(5, 3.2)}L${p(5, 11)}Z`
    + rect(-5, 4.6, 5, 6) + rect(-5, 7.8, 5, 9.2);
}

export function drawIcon(id: string): IconImage | null {
  if (typeof document === 'undefined') return null;
  const size = 44;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  const c = size / 2;
  if (id === 'ql-heliport' || id === 'ql-heliport-hospital') {
    const color = id === 'ql-heliport' ? HELIPAD_BLUE : HOSPITAL_RED;
    ctx.beginPath();
    ctx.arc(c, c, c - 3, 0, Math.PI * 2);
    ctx.fillStyle = '#ffffff';
    ctx.fill();
    ctx.lineWidth = 4;
    ctx.strokeStyle = color;
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.font = '700 23px -apple-system, "Helvetica Neue", Arial, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('H', c, c + 1.5);
  } else if (id === 'ql-fire') {
    roundRect(ctx, 3, 3, size - 6, size - 6, 10);
    ctx.fillStyle = FIRE_RED;
    ctx.fill();
    ctx.lineWidth = 2.5;
    ctx.strokeStyle = '#ffffff';
    ctx.stroke();
    ctx.fillStyle = '#ffffff';
    ctx.fill(new Path2D(stationPath(c, c + 0.5, 25)), 'evenodd');
  } else {
    return null;
  }
  return { width: size, height: size, data: ctx.getImageData(0, 0, size, size).data };
}
