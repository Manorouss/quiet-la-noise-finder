export type DenseRecord = number[];
export type DensePeriod = 'D' | 'E' | 'N';
export type DenseScenario = '23' | '585';
export type DenseLod = 'overview' | 'neighborhood' | 'exact';
export type ContextLayerId = 'county-fire' | 'city-fire' | 'heliports' | 'airport-contours';
export type ContextVisibility = Record<ContextLayerId, boolean>;

export type BBox = [west: number, south: number, east: number, north: number];
export type DenseBounds = { west: number; south: number; east: number; north: number };

export interface DenseShard {
  id: string;
  path: string;
  bbox_wgs84: BBox;
  receiver_count?: number;
}

export interface DenseImage {
  path: string;
  scenario: DenseScenario;
  period: DensePeriod;
}

export interface DenseManifest {
  bounds_wgs84: BBox;
  display_scale: { low: number; high: number };
  lod: Record<Exclude<DenseLod, 'exact'>, { path: string }>;
  modeled_sources?: { scenarios?: Array<{ scenario: DenseScenario; path: string; source_count?: number }> };
  shards: DenseShard[];
  stable_field: { coordinates: [[number, number], [number, number], [number, number], [number, number]]; images?: DenseImage[] };
}

export interface SceneManifest {
  region?: string;
  status?: string;
  view_bounds?: BBox;
  camera?: { pitch?: number; bearing?: number; zoom?: number };
  terrain?: { tile_zoom_range?: [number, number]; tiles?: Array<{ path: string }> };
  buildings?: { tile_zoom?: number; tiles?: Array<{ path: string }>; feature_count?: number };
}

export const DENSE_MANIFEST_URL = '/_local-data/dense/dense/manifest.json';
export const DENSE_SCENE_MANIFEST_URL = '/_local-data/dense/scene3d/scene-manifest.json';
export const DENSE_ROOT = '/_local-data/dense/dense/';
export const DENSE_SCENE_ROOT = '/_local-data/dense/scene3d/';
export const CONTEXT_ROOT = '/_local-data/context/';
export const CONTEXT_PATHS: Record<ContextLayerId, string> = {
  'county-fire': `${CONTEXT_ROOT}la_county_fire_stations.geojson`,
  'city-fire': `${CONTEXT_ROOT}la_city_fire_stations.geojson`,
  heliports: `${CONTEXT_ROOT}la_county_heliports.geojson`,
  'airport-contours': `${CONTEXT_ROOT}la_county_airport_noise_contours.geojson`,
};
export const DEFAULT_BOUNDS: BBox = [-118.6003758, 34.1545188, -118.5348193, 34.1864095];
export const SCENE_BOUNDS: BBox = [-118.6119389, 34.1456205, -118.5230159, 34.1954279];
export const EXACT_ZOOM = 14.7;
export const NEIGHBORHOOD_ZOOM = 13;
export const DEFAULT_MAX_ZOOM = 17;

const FIELD_INDEX: Record<DenseScenario, Record<DensePeriod, number>> = {
  '23': { D: 4, E: 5, N: 6 },
  '585': { D: 7, E: 8, N: 9 },
};

export function lodForZoom(zoom: number): DenseLod {
  if (zoom >= EXACT_ZOOM) return 'exact';
  if (zoom >= NEIGHBORHOOD_ZOOM) return 'neighborhood';
  return 'overview';
}

export function boundsObject(bbox: BBox): DenseBounds {
  return { west: bbox[0], south: bbox[1], east: bbox[2], north: bbox[3] };
}

export function bboxIntersects(a: BBox | DenseBounds, b: BBox | DenseBounds): boolean {
  const aa = Array.isArray(a) ? a : [a.west, a.south, a.east, a.north] as BBox;
  const bb = Array.isArray(b) ? b : [b.west, b.south, b.east, b.north] as BBox;
  return !(aa[2] < bb[0] || aa[0] > bb[2] || aa[3] < bb[1] || aa[1] > bb[3]);
}

export function pointInBounds(lng: number, lat: number, bbox: BBox): boolean {
  return lng >= bbox[0] && lng <= bbox[2] && lat >= bbox[1] && lat <= bbox[3];
}

export function expandPoint(lng: number, lat: number, meters: number): BBox {
  const latDelta = meters / 110540;
  const lngDelta = meters / (111320 * Math.max(0.25, Math.cos(lat * Math.PI / 180)));
  return [lng - lngDelta, lat - latDelta, lng + lngDelta, lat + latDelta];
}

export function visibleShards(manifest: DenseManifest, bounds: DenseBounds | BBox): DenseShard[] {
  return manifest.shards.filter((shard) => bboxIntersects(shard.bbox_wgs84, bounds)).sort((a, b) => a.id.localeCompare(b.id));
}

export function pointShards(manifest: DenseManifest, lng: number, lat: number, radiusM = 30): DenseShard[] {
  return visibleShards(manifest, expandPoint(lng, lat, radiusM));
}

export function recordValue(record: DenseRecord, scenario: DenseScenario, period: DensePeriod): number {
  return Number(record[FIELD_INDEX[scenario][period]]);
}

export function normalizeValue(value: number, manifest: DenseManifest): number {
  const span = manifest.display_scale.high - manifest.display_scale.low || 1;
  return Math.max(0, Math.min(1, (Number(value) - manifest.display_scale.low) / span));
}

export function recordCoordinates(record: DenseRecord): [number, number] {
  return [Number(record[1]), Number(record[2])];
}

export function recordsForScenario(records: DenseRecord[], scenario: DenseScenario, period: DensePeriod): DenseRecord[] {
  return records.filter((record) => {
    const value = recordValue(record, scenario, period);
    return Number.isFinite(value) && value !== -99;
  });
}

export function nearestRecord(records: DenseRecord[], lng: number, lat: number, maxDistanceM = 30): { record: DenseRecord; distanceM: number } | null {
  let best: { record: DenseRecord; distanceM: number } | null = null;
  for (const record of records) {
    const [recordLng, recordLat] = recordCoordinates(record);
    const distanceM = haversineMeters(lng, lat, recordLng, recordLat);
    if (distanceM <= maxDistanceM && (!best || distanceM < best.distanceM)) best = { record, distanceM };
  }
  return best;
}

export function haversineMeters(lngA: number, latA: number, lngB: number, latB: number): number {
  const radius = 6371008.8;
  const dLat = (latB - latA) * Math.PI / 180;
  const dLng = (lngB - lngA) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(latA * Math.PI / 180) * Math.cos(latB * Math.PI / 180) * Math.sin(dLng / 2) ** 2;
  return radius * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0, 1 - a)));
}

export function periodIndex(scenario: DenseScenario, period: DensePeriod): number {
  return FIELD_INDEX[scenario][period];
}

export function imagePath(style: 'field' | 'bands' | 'glow', scenario: DenseScenario, period: DensePeriod): string {
  return `${DENSE_ROOT}${style === 'field' ? 'fields' : `styles/${style}`}/${scenario}-${period}.webp`;
}

export function sourcePath(manifest: DenseManifest, scenario: DenseScenario): string | null {
  const entry = manifest.modeled_sources?.scenarios?.find((item) => item.scenario === scenario);
  return entry ? `${DENSE_ROOT}${entry.path}` : null;
}

export function sceneTilePath(path: string): string {
  return `${DENSE_SCENE_ROOT}${path}`;
}
