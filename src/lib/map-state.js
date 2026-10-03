// This payload descends from the four exports invalidated by the vertical audit.
// Corrected outputs must enter through a new, independently reviewed binding.
export const WITHHELD_LAYERS = Object.freeze({
  four_region_freeway_relative: 'Withheld: legacy results contain an elevation error. Corrected regional results are not connected to this portal.',
});

export function layerDisplayAllowed(id) {
  return !Object.hasOwn(WITHHELD_LAYERS, id);
}

export function receiverValue(value) {
  // Missing values and the exact engine sentinel must never become zero/quiet.
  return typeof value === 'number' && Number.isFinite(value) && value !== -99 ? value : null;
}

const familyLayers = {
  'Four-region freeway model': 'four_region_freeway_relative',
  'Tarzana mixed-road scenario': 'tarzana_mixed_road_scenario',
  'Airport planning contours': 'airport_planning_contours',
  'Source-341 incomplete mask': 'source_341_incomplete_mask',
};

export function inspectionForView(records, tarzanaById, period, view, toggles) {
  return records.flatMap((record) => {
    const layer = familyLayers[record.family];
    const modeled = layer === 'four_region_freeway_relative' || layer === 'tarzana_mixed_road_scenario';
    if (!layer || !layerDisplayAllowed(layer) || !toggles[layer] || (view === 'context' && modeled) || (view === 'modeled' && !modeled)) return [];
    if (layer !== 'tarzana_mixed_road_scenario') return [record];
    const receiver = tarzanaById.get(record.id);
    if (!receiver) return [];
    return [{ ...record, period, value: receiverValue(receiver[period.toLowerCase()]) }];
  });
}

export function fitPadding(width, height) {
  // Reserve actual control space, while retaining a usable mobile map viewport.
  if (width <= 640) return { top: Math.min(400, height * 0.45), right: 64, bottom: Math.min(220, height * 0.25), left: 24 };
  return { top: 150, right: 110, bottom: 190, left: 390 };
}
