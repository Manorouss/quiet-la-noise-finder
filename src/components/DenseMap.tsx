'use client';

import { useCallback, useEffect, useRef } from 'react';
import type { Map as MapLibreMap } from 'maplibre-gl';
import {
  DEFAULT_BOUNDS,
  DEFAULT_MAX_ZOOM,
  CONTEXT_PATHS,
  DENSE_MANIFEST_URL,
  DENSE_SCENE_MANIFEST_URL,
  DENSE_ROOT,
  DENSE_SCENE_ROOT,
  SCENE_BOUNDS,
  boundsObject,
  bboxIntersects,
  imagePath,
  lodForZoom,
  nearestRecord,
  pointInBounds,
  pointShards,
  recordCoordinates,
  recordValue,
  recordsForScenario,
  sceneTilePath,
  sourcePath,
  visibleShards,
  type DenseLod,
  type DenseManifest,
  type DensePeriod,
  type DenseRecord,
  type DenseScenario,
  type SceneManifest,
  type ContextLayerId,
  type ContextVisibility,
} from '@/lib/dense-map';

export type { DenseRecord } from '@/lib/dense-map';

export type DenseStatus = {
  loading: boolean;
  error: string | null;
  visibleCount: number;
  detail: string;
  inCoverage: boolean;
  threeDLoading: boolean;
};

export type Camera = { lng: number; lat: number; zoom: number; pitch: number; bearing: number };

export type ContextSelection = {
  layer: ContextLayerId;
  title: string;
  detail: string;
  note: string;
};

export interface DenseMapProps {
  period: DensePeriod;
  scenario: DenseScenario;
  treatment: 'field' | 'bands' | 'dots' | 'glow';
  mode3d: boolean;
  sourcesVisible: boolean;
  contextVisibility: ContextVisibility;
  selected: DenseRecord | null;
  fitRequest: number;
  fitContextRequest: number;
  target: { lng: number; lat: number; nonce: number; zoom?: number } | null;
  onSelect: (record: DenseRecord | null, distanceM: number) => void;
  onContextSelect: (selection: ContextSelection | null) => void;
  onStatus: (status: DenseStatus) => void;
  onCamera: (camera: Camera) => void;
  onModeFallback: () => void;
}

type Feature = {
  type: 'Feature';
  id?: string | number;
  geometry: { type: string; coordinates: unknown };
  properties: Record<string, unknown>;
};
type FeatureCollection = { type: 'FeatureCollection'; features: Feature[] };

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };
const COLORS = ['#4f9f8d', '#a8d89b', '#f3e983', '#f4ae54', '#e46d3f', '#9c2853'];

function featureCollection(features: Feature[]): FeatureCollection { return { type: 'FeatureCollection', features }; }

async function fetchBytes(path: string): Promise<Uint8Array> {
  const response = await fetch(path, { cache: 'no-store' });
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return new Uint8Array(await response.arrayBuffer());
}

async function fetchJson<T>(path: string): Promise<T> {
  return JSON.parse(new TextDecoder().decode(await fetchBytes(path))) as T;
}

async function fetchGzipJson<T>(path: string): Promise<T> {
  const bytes = await fetchBytes(path);
  if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) return JSON.parse(new TextDecoder().decode(bytes)) as T;
  if (typeof DecompressionStream === 'undefined') throw new Error('This browser cannot decode the packaged dense assets.');
  const stream = new Blob([bytes.buffer as ArrayBuffer]).stream().pipeThrough(new DecompressionStream('gzip'));
  return JSON.parse(await new Response(stream).text()) as T;
}

function boundsForMap(map: MapLibreMap) {
  const bounds = map.getBounds();
  return boundsObject([bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()]);
}

function coordinatesFromManifest(manifest: DenseManifest): [[number, number], [number, number], [number, number], [number, number]] {
  return manifest.stable_field?.coordinates ?? [
    [DEFAULT_BOUNDS[0], DEFAULT_BOUNDS[3]], [DEFAULT_BOUNDS[2], DEFAULT_BOUNDS[3]],
    [DEFAULT_BOUNDS[2], DEFAULT_BOUNDS[1]], [DEFAULT_BOUNDS[0], DEFAULT_BOUNDS[1]],
  ];
}

function recordFeature(record: DenseRecord, scenario: DenseScenario, period: DensePeriod, manifest: DenseManifest): Feature {
  const [lng, lat] = recordCoordinates(record);
  const value = recordValue(record, scenario, period);
  const otherScenario: DenseScenario = scenario === '23' ? '585' : '23';
  return {
    type: 'Feature', id: Number(record[0]), geometry: { type: 'Point', coordinates: [lng, lat] },
    properties: {
      receiver_id: Number(record[0]), family_code: Number(record[3]), value,
      other_value: recordValue(record, otherScenario, period),
      normalized: Math.max(0, Math.min(1, (value - manifest.display_scale.low) / (manifest.display_scale.high - manifest.display_scale.low || 1))),
    },
  };
}

function recordsToFeatures(records: DenseRecord[], scenario: DenseScenario, period: DensePeriod, manifest: DenseManifest): FeatureCollection {
  return featureCollection(recordsForScenario(records, scenario, period).map((record) => recordFeature(record, scenario, period, manifest)));
}

function columnsFromFeatures(points: FeatureCollection, map: MapLibreMap): FeatureCollection {
  const bounds = map.getBounds();
  const inView = points.features.filter((feature) => {
    const [lng, lat] = feature.geometry.coordinates as [number, number];
    return lng >= bounds.getWest() && lng <= bounds.getEast() && lat >= bounds.getSouth() && lat <= bounds.getNorth();
  });
  const stride = Math.max(1, Math.ceil(inView.length / 2200));
  const features: Feature[] = [];
  for (let index = 0; index < inView.length; index += stride) {
    const point = inView[index];
    const [lng, lat] = point.geometry.coordinates as [number, number];
    const radiusM = Number(point.properties.family_code) === 1 ? 2.5 : 4;
    const latDelta = radiusM / 110540;
    const lngDelta = radiusM / (111320 * Math.max(0.25, Math.cos(lat * Math.PI / 180)));
    const ring: [number, number][] = [];
    for (let side = 0; side < 6; side += 1) {
      const angle = Math.PI / 3 * side;
      ring.push([lng + Math.cos(angle) * lngDelta, lat + Math.sin(angle) * latDelta]);
    }
    ring.push(ring[0]);
    features.push({
      type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] },
      properties: { receiver_id: point.properties.receiver_id, normalized: Number(point.properties.normalized), column_height_m: 3 + Number(point.properties.normalized) * 25, display_only: true },
    });
  }
  return featureCollection(features);
}

function isValidRecord(record: unknown): record is DenseRecord {
  if (!Array.isArray(record) || record.length !== 10 || !record.every((value) => typeof value === 'number' && Number.isFinite(value))) return false;
  const receiverId = record[0];
  const familyCode = record[3];
  return receiverId > 0 && (familyCode === 0 || familyCode === 1);
}

const CONTEXT_LAYER_IDS: ContextLayerId[] = ['county-fire', 'city-fire', 'heliports', 'airport-contours'];
const contextSourceId = (id: ContextLayerId) => `context-${id}`;
const contextLayerId = (id: ContextLayerId) => `context-${id}`;

function contextSelection(id: ContextLayerId, properties: Record<string, unknown>): ContextSelection {
  if (id === 'airport-contours') {
    const airport = String(properties.AIRPORT_NAME || 'Airport');
    const contourClass = String(properties.CLASS || '');
    return {
      layer: id,
      title: `${airport} contour${contourClass ? ` · ${contourClass} CNEL` : ''}`,
      detail: 'Airport noise contour location',
      note: 'Context geometry only. Aircraft activity and this contour do not contribute to the surface-road value.',
    };
  }
  if (id === 'heliports') {
    const name = String(properties.name || 'Heliport');
    const city = properties.city ? ` · ${String(properties.city)}` : '';
    return { layer: id, title: `${name}${city}`, detail: 'Heliport location', note: 'Location context only. Flight activity and helicopter sound are not modeled in this surface.' };
  }
  const city = properties.city ? ` · ${String(properties.city)}` : '';
  const station = properties.station ? `Station ${String(properties.station)}` : 'Fire station';
  return {
    layer: id,
    title: `${station}${city}`,
    detail: id === 'city-fire' ? 'City of Los Angeles fire station' : 'LA County fire station',
    note: 'Location context only. Dispatch, apparatus, siren, and aircraft activity are not modeled in this surface.',
  };
}

function detailLabel(lod: DenseLod): string {
  return lod === 'exact' ? 'Exact receivers' : lod === 'neighborhood' ? 'Neighborhood sample' : 'Overview sample';
}

export default function DenseMap({ period, scenario, treatment, mode3d, sourcesVisible, contextVisibility, selected, fitRequest, fitContextRequest, target, onSelect, onContextSelect, onStatus, onCamera, onModeFallback }: DenseMapProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const manifestRef = useRef<DenseManifest | null>(null);
  const sceneRef = useRef<SceneManifest | null>(null);
  const recordsRef = useRef<DenseRecord[]>([]);
  const activeLodRef = useRef<DenseLod>('overview');
  const datasetKeyRef = useRef('');
  const requestTokenRef = useRef(0);
  const clickTokenRef = useRef(0);
  const errorRef = useRef<string | null>(null);
  const sourceTokenRef = useRef(0);
  const cacheRef = useRef(new Map<string, Promise<unknown>>());
  const contextDataRef = useRef(new Map<ContextLayerId, FeatureCollection>());
  const sceneLoadedRef = useRef(false);
  const sceneLoadingRef = useRef(false);
  const scenePromiseRef = useRef<Promise<SceneManifest | null> | null>(null);
  const sceneFailedRef = useRef(false);
  const disposedRef = useRef(false);
  const propsRef = useRef({ period, scenario, treatment, mode3d, sourcesVisible, contextVisibility, selected, target });
  const callbacksRef = useRef({ onSelect, onContextSelect, onStatus, onCamera, onModeFallback });
  propsRef.current = { period, scenario, treatment, mode3d, sourcesVisible, contextVisibility, selected, target };
  callbacksRef.current = { onSelect, onContextSelect, onStatus, onCamera, onModeFallback };

  const status = useCallback((next: Partial<DenseStatus>) => {
    if ('error' in next) errorRef.current = next.error ?? null;
    callbacksRef.current.onStatus({ loading: false, error: errorRef.current, visibleCount: recordsRef.current.length, detail: detailLabel(activeLodRef.current), inCoverage: recordsRef.current.length > 0, threeDLoading: sceneLoadingRef.current, ...next });
  }, []);

  const fetchCached = useCallback(async <T,>(path: string, gzip = false): Promise<T> => {
    const cached = cacheRef.current.get(path);
    if (cached) return cached as Promise<T>;
    const promise = (gzip ? fetchGzipJson<T>(path) : fetchJson<T>(path)).catch((error) => { cacheRef.current.delete(path); throw error; });
    cacheRef.current.set(path, promise);
    return promise;
  }, []);

  const setData = useCallback((sourceId: string, data: unknown) => {
    const map = mapRef.current;
    if (!map) return;
    const source = map.getSource(sourceId) as import('maplibre-gl').GeoJSONSource | undefined;
    source?.setData(data as never);
  }, []);

  const fitCoverage = useCallback(() => {
    const map = mapRef.current;
    const manifest = manifestRef.current;
    if (!map) return;
    const bbox = manifest?.bounds_wgs84 ?? DEFAULT_BOUNDS;
    map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: window.innerWidth <= 720 ? { top: 24, right: 60, bottom: 260, left: 24 } : { top: 24, right: 24, bottom: 70, left: 24 }, duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 550 });
  }, []);

  const updateDataSources = useCallback((records: DenseRecord[]) => {
    const map = mapRef.current;
    const manifest = manifestRef.current;
    if (!map || !manifest) return;
    const { scenario: currentScenario, period: currentPeriod } = propsRef.current;
    const points = recordsToFeatures(records, currentScenario, currentPeriod, manifest);
    recordsRef.current = records;
    setData('dense-dots', points);
    setData('dense-columns', columnsFromFeatures(points, map));
    const selectedRecord = propsRef.current.selected;
    const selectedPoint = selectedRecord && isValidRecord(selectedRecord) ? recordFeature(selectedRecord, currentScenario, currentPeriod, manifest) : null;
    setData('selected-receiver', selectedPoint ? featureCollection([selectedPoint]) : EMPTY);
    status({ visibleCount: records.length, detail: detailLabel(activeLodRef.current), inCoverage: bboxIntersects(boundsForMap(map), manifest.bounds_wgs84) });
  }, [setData, status]);

  const loadView = useCallback(async (force = false) => {
    const map = mapRef.current;
    const manifest = manifestRef.current;
    if (!map || !manifest) return;
    const lod = lodForZoom(map.getZoom());
    const shards = lod === 'exact' ? visibleShards(manifest, boundsForMap(map)) : [];
    const datasetKey = lod === 'exact' ? `exact:${shards.map((shard) => shard.id).join(',')}` : lod;
    if (!force && datasetKeyRef.current === datasetKey) {
      status({ visibleCount: recordsRef.current.length, detail: detailLabel(activeLodRef.current), inCoverage: bboxIntersects(boundsForMap(map), manifest.bounds_wgs84) });
      return;
    }
    datasetKeyRef.current = datasetKey;
    activeLodRef.current = lod;
    const token = ++requestTokenRef.current;
    status({ loading: true, error: null, detail: lod === 'exact' ? 'Loading exact receivers…' : `Loading ${detailLabel(lod).toLowerCase()}…`, inCoverage: false });
    try {
      const payloads = lod === 'exact'
        ? await Promise.all(shards.map((shard) => fetchCached<{ records?: unknown[] }>(`${DENSE_ROOT}${shard.path}`, true)))
        : [await fetchCached<{ records?: unknown[] }>(`${DENSE_ROOT}${manifest.lod[lod].path}`, true)];
      if (disposedRef.current || token !== requestTokenRef.current) return;
      const records = payloads.flatMap((payload) => payload.records ?? []).filter(isValidRecord);
      updateDataSources(records);
      status({ loading: false, error: null, visibleCount: records.length, detail: detailLabel(lod), inCoverage: bboxIntersects(boundsForMap(map), manifest.bounds_wgs84) });
    } catch (error) {
      if (disposedRef.current || token !== requestTokenRef.current) return;
      datasetKeyRef.current = '';
      status({ loading: false, error: error instanceof Error ? error.message : 'Dense map data could not load.', detail: detailLabel(lod), inCoverage: false });
    }
  }, [fetchCached, status, updateDataSources]);

  const loadExactAt = useCallback(async (lng: number, lat: number, token: number): Promise<{ record: DenseRecord; distanceM: number } | null | undefined> => {
    const manifest = manifestRef.current;
    if (!manifest || !pointInBounds(lng, lat, manifest.bounds_wgs84)) return null;
    const shards = pointShards(manifest, lng, lat, 30);
    if (!shards.length) return null;
    try {
      const payloads = await Promise.all(shards.map((shard) => fetchCached<{ records?: unknown[] }>(`${DENSE_ROOT}${shard.path}`, true)));
      if (token !== clickTokenRef.current) return null;
      return nearestRecord(recordsForScenario(payloads.flatMap((payload) => payload.records ?? []).filter(isValidRecord), propsRef.current.scenario, propsRef.current.period), lng, lat, 30);
    } catch (error) {
      if (token === clickTokenRef.current) status({ error: error instanceof Error ? `Exact receiver data could not load: ${error.message}` : 'Exact receiver data could not load.' });
      return undefined;
    }
  }, [fetchCached, status]);

  const loadSources = useCallback(async (currentScenario: DenseScenario) => {
    const manifest = manifestRef.current;
    const map = mapRef.current;
    if (!manifest || !map?.getSource('modeled-sources')) return;
    const token = ++sourceTokenRef.current;
    const path = sourcePath(manifest, currentScenario);
    setData('modeled-sources', EMPTY);
    if (!path) { setData('modeled-sources', EMPTY); return; }
    try {
      const collection = await fetchCached<FeatureCollection>(path, true);
      if (token === sourceTokenRef.current && !disposedRef.current) setData('modeled-sources', collection);
    } catch (error) {
      if (token === sourceTokenRef.current && !disposedRef.current) status({ error: error instanceof Error ? `Modeled source data could not load: ${error.message}` : 'Modeled source data could not load.' });
    }
  }, [fetchCached, setData, status]);

  const loadContext = useCallback(async (id: ContextLayerId) => {
    const map = mapRef.current;
    if (!map?.getSource(contextSourceId(id))) return;
    try {
      const collection = await fetchCached<FeatureCollection>(CONTEXT_PATHS[id]);
      if (!disposedRef.current) { contextDataRef.current.set(id, collection); setData(contextSourceId(id), collection); }
    } catch (error) {
      if (!disposedRef.current) status({ error: error instanceof Error ? `Context layer could not load: ${error.message}` : 'Context layer could not load.' });
    }
  }, [fetchCached, setData, status]);

  const fitVisibleContext = useCallback(async () => {
    const map = mapRef.current;
    if (!map) return;
    const visible = CONTEXT_LAYER_IDS.filter((id) => propsRef.current.contextVisibility[id]);
    await Promise.all(visible.filter((id) => !contextDataRef.current.has(id)).map((id) => loadContext(id)));
    const coordinates: [number, number][] = [];
    const collect = (value: unknown) => {
      if (!Array.isArray(value)) return;
      if (value.length === 2 && value.every((part) => typeof part === 'number' && Number.isFinite(part))) { coordinates.push([Number(value[0]), Number(value[1])]); return; }
      value.forEach(collect);
    };
    visible.forEach((id) => contextDataRef.current.get(id)?.features.forEach((feature) => collect(feature.geometry.coordinates)));
    if (!coordinates.length) return;
    let west = Infinity; let east = -Infinity; let south = Infinity; let north = -Infinity;
    for (const [lng, lat] of coordinates) { west = Math.min(west, lng); east = Math.max(east, lng); south = Math.min(south, lat); north = Math.max(north, lat); }
    map.fitBounds([[west, south], [east, north]], { padding: window.innerWidth <= 720 ? { top: 90, right: 40, bottom: 250, left: 24 } : { top: 80, right: 30, bottom: 80, left: 30 }, duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 550 });
  }, [loadContext]);

  const loadScene = useCallback(async () => {
    if (sceneLoadedRef.current) return sceneRef.current;
    if (sceneLoadingRef.current) return scenePromiseRef.current;
    sceneLoadingRef.current = true;
    status({ loading: true, threeDLoading: true, detail: detailLabel(activeLodRef.current) });
    const promise = (async (): Promise<SceneManifest | null> => { try {
      const scene = await fetchCached<SceneManifest>(DENSE_SCENE_MANIFEST_URL);
      if (scene.region && scene.region !== 'tarzana') throw new Error('The packaged 3D scene is for an unexpected region.');
      if (scene.status && scene.status !== 'research_inspection_only') throw new Error('The 3D scene is not marked inspection-only.');
      const tileEntries = scene.buildings?.tiles ?? [];
      const collections = await Promise.all(tileEntries.map((tile) => fetchCached<{ features?: Feature[] }>(sceneTilePath(tile.path), true)));
      const features = collections.flatMap((collection) => collection.features ?? []);
      const expected = scene.buildings?.feature_count;
      if (expected && features.length !== expected) throw new Error('The packaged building scene count changed.');
      if (disposedRef.current) return scene;
      setData('buildings-3d', featureCollection(features));
      sceneRef.current = scene;
      sceneLoadedRef.current = true;
      status({ loading: false, threeDLoading: false, error: null });
      return scene;
    } catch (error) {
      sceneFailedRef.current = true;
      status({ loading: false, threeDLoading: false, error: error instanceof Error ? error.message : '3D scene unavailable.' });
      return null;
    } finally { sceneLoadingRef.current = false; }})();
    scenePromiseRef.current = promise;
    const scene = await promise;
    scenePromiseRef.current = null;
    return scene;
  }, [fetchCached, setData, status]);

  const applyMode3d = useCallback(async (enabled: boolean) => {
    const map = mapRef.current;
    if (!map) return;
    if (!enabled) {
      map.dragRotate.disable();
      map.touchZoomRotate.disableRotation();
      map.touchPitch.disable();
      map.setTerrain(null);
      map.setMaxZoom(DEFAULT_MAX_ZOOM);
      for (const id of ['buildings-3d', 'dense-columns']) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'none');
      map.easeTo({ pitch: 0, bearing: 0, duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 420 });
      if (manifestRef.current) updateDataSources(recordsRef.current);
      status({ threeDLoading: false });
      return;
    }
    const scene = await loadScene();
    if (!scene) {
      if (!sceneFailedRef.current) sceneFailedRef.current = true;
      applyMode3d(false);
      callbacksRef.current.onModeFallback();
      return;
    }
    if (!mapRef.current || disposedRef.current || !propsRef.current.mode3d) return;
    const center = map.getCenter();
    const zoom = map.getZoom();
    map.dragRotate.enable();
    map.touchZoomRotate.enableRotation();
    map.touchPitch.enable();
    map.setTerrain({ source: 'terrain-rgb', exaggeration: 1 });
    if (map.getLayer('buildings-3d')) map.setLayoutProperty('buildings-3d', 'visibility', 'visible');
    if (map.getLayer('dense-columns')) map.setLayoutProperty('dense-columns', 'visibility', propsRef.current.treatment === 'dots' ? 'visible' : 'none');
    map.easeTo({ center, zoom, pitch: scene.camera?.pitch ?? 66, bearing: scene.camera?.bearing ?? -22, duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 650 });
    status({ loading: false, threeDLoading: false, error: null });
  }, [loadScene, status, updateDataSources]);

  useEffect(() => {
    let cancelled = false;
    disposedRef.current = false;
    void import('maplibre-gl').then(async (module) => {
      if (cancelled || !hostRef.current || mapRef.current) return;
      module.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');
      const map = new module.Map({
        container: hostRef.current,
        style: { version: 8, sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, maxzoom: 19 } }, layers: [{ id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-opacity': 0.84 } }] },
        center: [-118.57, 34.17], zoom: 12.3, minZoom: 7, maxZoom: DEFAULT_MAX_ZOOM, pitch: 0, bearing: 0, dragRotate: false, pitchWithRotate: true, attributionControl: false,
      });
      map.touchZoomRotate.disableRotation();
      map.touchPitch.disable();
      mapRef.current = map;
      (window as unknown as { __quietMap?: MapLibreMap }).__quietMap = map;
      let styleReady = false;
      map.on('error', (event) => {
        if (cancelled) return;
        const message = event.error?.message ?? 'Map data failed to render.';
        if (/terrain|building|scene/i.test(message)) {
          status({ error: message });
          if (propsRef.current.mode3d) { void applyMode3d(false); callbacksRef.current.onModeFallback(); }
        } else if (/dense/i.test(message)) status({ error: message });
      });
      const initializeOverlays = () => {
        if (cancelled || !styleReady || !manifestRef.current || map.getLayer('dense-field')) return;
        const manifest = manifestRef.current;
        if (!manifest) return;
        const coords = coordinatesFromManifest(manifest);
        map.addSource('dense-dots', { type: 'geojson', data: EMPTY as never });
        map.addSource('dense-columns', { type: 'geojson', data: EMPTY as never });
        map.addSource('selected-receiver', { type: 'geojson', data: EMPTY as never });
        map.addSource('buildings-3d', { type: 'geojson', data: EMPTY as never });
        map.addSource('modeled-sources', { type: 'geojson', data: EMPTY as never });
        for (const id of CONTEXT_LAYER_IDS) map.addSource(contextSourceId(id), { type: 'geojson', data: EMPTY as never });
        map.addSource('terrain-rgb', { type: 'raster-dem', tiles: [`${DENSE_SCENE_ROOT}terrain/tarzana/{z}/{x}/{y}.png`], tileSize: 256, minzoom: 8, maxzoom: 14, bounds: SCENE_BOUNDS as never, encoding: 'mapbox' });
        for (const style of ['field', 'glow', 'bands'] as const) map.addSource(`dense-${style}`, { type: 'image', url: imagePath(style, propsRef.current.scenario, propsRef.current.period), coordinates: coords });
        map.addLayer({ id: 'dense-field', type: 'raster', source: 'dense-field', layout: { visibility: propsRef.current.treatment === 'field' ? 'visible' : 'none' }, paint: { 'raster-opacity': 1, 'raster-fade-duration': 0, 'raster-resampling': 'linear' } });
        map.addLayer({ id: 'dense-glow', type: 'raster', source: 'dense-glow', layout: { visibility: propsRef.current.treatment === 'glow' ? 'visible' : 'none' }, paint: { 'raster-opacity': 1, 'raster-fade-duration': 0, 'raster-resampling': 'linear' } });
        map.addLayer({ id: 'dense-bands', type: 'raster', source: 'dense-bands', layout: { visibility: propsRef.current.treatment === 'bands' ? 'visible' : 'none' }, paint: { 'raster-opacity': 1, 'raster-fade-duration': 0, 'raster-resampling': 'nearest' } });
        map.addLayer({ id: 'modeled-sources-casing', type: 'line', source: 'modeled-sources', layout: { visibility: propsRef.current.sourcesVisible ? 'visible' : 'none' }, paint: { 'line-color': '#f7f2d9', 'line-width': ['interpolate', ['linear'], ['zoom'], 11.4, 2.4, 15.2, 5.2], 'line-opacity': 0.9 } });
        map.addLayer({ id: 'modeled-sources-line', type: 'line', source: 'modeled-sources', layout: { visibility: propsRef.current.sourcesVisible ? 'visible' : 'none' }, paint: { 'line-color': '#263d45', 'line-width': ['interpolate', ['linear'], ['zoom'], 11.4, 1, 15.2, 2.4], 'line-opacity': 0.85 } });
        const colorExpression = ['interpolate', ['linear'], ['get', 'normalized'], 0, COLORS[0], .2, COLORS[1], .42, COLORS[2], .62, COLORS[3], .8, COLORS[4], 1, COLORS[5]] as never;
        map.addLayer({ id: 'dense-dots', type: 'circle', source: 'dense-dots', layout: { visibility: propsRef.current.treatment === 'dots' && !propsRef.current.mode3d ? 'visible' : 'none' }, paint: { 'circle-color': colorExpression, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 2.6, 15, 5.1, 18, 9], 'circle-opacity': 0.9, 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 12, .2, 16, 1], 'circle-stroke-color': 'rgba(255,255,255,.92)' } });
        map.addLayer({ id: 'buildings-3d', type: 'fill-extrusion', source: 'buildings-3d', minzoom: 12, layout: { visibility: 'none' }, paint: { 'fill-extrusion-color': ['step', ['coalesce', ['get', 'h'], 0], '#4e626d', 2, '#718b86', 5, '#98a477', 10, '#b89d68', 20, '#c57a5c', 60, '#9f4652'], 'fill-extrusion-height': ['coalesce', ['get', 'h'], 0], 'fill-extrusion-base': 0, 'fill-extrusion-opacity': .9, 'fill-extrusion-vertical-gradient': true } });
        map.addLayer({ id: 'dense-columns', type: 'fill-extrusion', source: 'dense-columns', layout: { visibility: 'none' }, paint: { 'fill-extrusion-color': colorExpression, 'fill-extrusion-height': ['interpolate', ['linear'], ['zoom'], 12, ['*', ['get', 'column_height_m'], .52], 15.2, ['get', 'column_height_m']], 'fill-extrusion-base': 0, 'fill-extrusion-opacity': .42, 'fill-extrusion-vertical-gradient': true } });
        map.addLayer({ id: 'selected-receiver', type: 'circle', source: 'selected-receiver', paint: { 'circle-radius': 9, 'circle-color': 'rgba(255,255,255,0)', 'circle-stroke-color': '#171b22', 'circle-stroke-width': 2.5 } });
        map.addLayer({ id: contextLayerId('airport-contours'), type: 'fill', source: contextSourceId('airport-contours'), layout: { visibility: propsRef.current.contextVisibility['airport-contours'] ? 'visible' : 'none' }, paint: { 'fill-color': '#816a9b', 'fill-opacity': 0.13 } });
        map.addLayer({ id: 'context-airport-contours-line', type: 'line', source: contextSourceId('airport-contours'), layout: { visibility: propsRef.current.contextVisibility['airport-contours'] ? 'visible' : 'none' }, paint: { 'line-color': '#675180', 'line-width': 1.4, 'line-opacity': 0.78 } });
        map.addLayer({ id: contextLayerId('county-fire'), type: 'circle', source: contextSourceId('county-fire'), layout: { visibility: propsRef.current.contextVisibility['county-fire'] ? 'visible' : 'none' }, paint: { 'circle-color': '#25756a', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 3, 13, 4.5, 17, 7], 'circle-stroke-color': '#f7f2d9', 'circle-stroke-width': 1.2, 'circle-opacity': 0.95 } });
        map.addLayer({ id: contextLayerId('city-fire'), type: 'circle', source: contextSourceId('city-fire'), layout: { visibility: propsRef.current.contextVisibility['city-fire'] ? 'visible' : 'none' }, paint: { 'circle-color': '#ca7a3c', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 3, 13, 4.5, 17, 7], 'circle-stroke-color': '#f7f2d9', 'circle-stroke-width': 1.2, 'circle-opacity': 0.95 } });
        map.addLayer({ id: contextLayerId('heliports'), type: 'circle', source: contextSourceId('heliports'), layout: { visibility: propsRef.current.contextVisibility.heliports ? 'visible' : 'none' }, paint: { 'circle-color': '#816a9b', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 3, 13, 4.5, 17, 7], 'circle-stroke-color': '#f7f2d9', 'circle-stroke-width': 1.2, 'circle-opacity': 0.95 } });
        map.on('click', async (event) => {
          const contextLayers = [contextLayerId('county-fire'), contextLayerId('city-fire'), contextLayerId('heliports'), contextLayerId('airport-contours')].filter((id) => map.getLayoutProperty(id, 'visibility') === 'visible');
          const contextFeature = contextLayers.length ? map.queryRenderedFeatures(event.point, { layers: contextLayers })[0] : undefined;
          if (contextFeature) {
            clickTokenRef.current += 1;
            const matched = CONTEXT_LAYER_IDS.find((id) => contextLayerId(id) === contextFeature.layer.id || (id === 'airport-contours' && contextFeature.layer.id === 'context-airport-contours'));
            if (matched) { callbacksRef.current.onContextSelect(contextSelection(matched, (contextFeature.properties ?? {}) as Record<string, unknown>)); return; }
          }
          callbacksRef.current.onContextSelect(null);
          const token = ++clickTokenRef.current;
          const currentManifest = manifestRef.current;
          if (!currentManifest || !pointInBounds(event.lngLat.lng, event.lngLat.lat, currentManifest.bounds_wgs84)) { callbacksRef.current.onSelect(null, 0); return; }
          const match = await loadExactAt(event.lngLat.lng, event.lngLat.lat, token);
          if (token !== clickTokenRef.current) return;
          if (match === undefined) return;
          callbacksRef.current.onSelect(match?.record ?? null, match?.distanceM ?? 0);
        });
        map.on('moveend', () => { callbacksRef.current.onCamera({ lng: map.getCenter().lng, lat: map.getCenter().lat, zoom: map.getZoom(), pitch: map.getPitch(), bearing: map.getBearing() }); void loadView(); });
        map.on('resize', () => { if (map.getCanvas().width) void loadView(); });
        const initialTarget = propsRef.current.target;
        if (initialTarget) map.jumpTo({ center: [initialTarget.lng, initialTarget.lat], zoom: initialTarget.zoom ?? 14.7 });
        else map.fitBounds([[manifest.bounds_wgs84[0], manifest.bounds_wgs84[1]], [manifest.bounds_wgs84[2], manifest.bounds_wgs84[3]]], { padding: window.innerWidth <= 720 ? { top: 24, right: 60, bottom: 260, left: 24 } : { top: 24, right: 24, bottom: 70, left: 24 }, duration: 0 });
        void loadView(true);
        void loadSources(propsRef.current.scenario);
        for (const id of CONTEXT_LAYER_IDS) if (propsRef.current.contextVisibility[id]) void loadContext(id);
        if (propsRef.current.mode3d) void applyMode3d(true);
      };
      map.once('style.load', () => { styleReady = true; initializeOverlays(); });
      initializeOverlays();
      try {
        manifestRef.current = await fetchCached<DenseManifest>(DENSE_MANIFEST_URL);
        if (cancelled) return;
        initializeOverlays();
      } catch (error) { status({ error: error instanceof Error ? error.message : 'Dense manifest unavailable.', inCoverage: false }); }
    }).catch((error) => { if (!cancelled) status({ error: error instanceof Error ? error.message : 'Map renderer unavailable.' }); });
    return () => { cancelled = true; disposedRef.current = true; mapRef.current?.remove(); mapRef.current = null; delete (window as unknown as { __quietMap?: unknown }).__quietMap; };
  }, [applyMode3d, fetchCached, loadContext, loadExactAt, loadSources, loadView, setData, status]);

  useEffect(() => {
    const map = mapRef.current;
    const manifest = manifestRef.current;
    if (!map || !manifest || !map.getLayer('dense-field')) return;
    for (const style of ['field', 'glow', 'bands'] as const) {
      const source = map.getSource(`dense-${style}`) as (import('maplibre-gl').ImageSource & { updateImage?: (options: { url: string }) => void }) | undefined;
      source?.updateImage?.({ url: imagePath(style, scenario, period) });
      if (map.getLayer(`dense-${style}`)) map.setLayoutProperty(`dense-${style}`, 'visibility', treatment === style ? 'visible' : 'none');
    }
    if (map.getLayer('dense-dots')) map.setLayoutProperty('dense-dots', 'visibility', treatment === 'dots' && !propsRef.current.mode3d ? 'visible' : 'none');
    if (map.getLayer('dense-columns')) map.setLayoutProperty('dense-columns', 'visibility', propsRef.current.mode3d && treatment === 'dots' ? 'visible' : 'none');
    if (map.getLayer('modeled-sources-casing')) map.setLayoutProperty('modeled-sources-casing', 'visibility', sourcesVisible ? 'visible' : 'none');
    if (map.getLayer('modeled-sources-line')) map.setLayoutProperty('modeled-sources-line', 'visibility', sourcesVisible ? 'visible' : 'none');
    for (const id of CONTEXT_LAYER_IDS) {
      const visible = contextVisibility[id] ? 'visible' : 'none';
      if (map.getLayer(contextLayerId(id))) map.setLayoutProperty(contextLayerId(id), 'visibility', visible);
      if (id === 'airport-contours' && map.getLayer('context-airport-contours-line')) map.setLayoutProperty('context-airport-contours-line', 'visibility', visible);
      if (contextVisibility[id]) void loadContext(id);
    }
    updateDataSources(recordsRef.current);
  }, [contextVisibility, loadContext, period, scenario, sourcesVisible, treatment, updateDataSources]);

  useEffect(() => { void loadSources(scenario); }, [loadSources, scenario]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer('dense-field')) return;
    if (map.getLayer('dense-dots')) map.setLayoutProperty('dense-dots', 'visibility', propsRef.current.treatment === 'dots' && !mode3d ? 'visible' : 'none');
    if (map.getLayer('dense-columns')) map.setLayoutProperty('dense-columns', 'visibility', mode3d && propsRef.current.treatment === 'dots' ? 'visible' : 'none');
    void applyMode3d(mode3d);
  }, [applyMode3d, mode3d]);

  useEffect(() => {
    const map = mapRef.current;
    const manifest = manifestRef.current;
    if (!map || !manifest || !map.getLayer('selected-receiver') || !selected || !isValidRecord(selected)) { if (map) setData('selected-receiver', EMPTY); return; }
    setData('selected-receiver', featureCollection([recordFeature(selected, scenario, period, manifest)]));
  }, [period, scenario, selected, setData]);

  useEffect(() => { if (fitRequest) fitCoverage(); }, [fitCoverage, fitRequest]);
  useEffect(() => { if (fitContextRequest) void fitVisibleContext(); }, [fitContextRequest, fitVisibleContext]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !target || !target.nonce) return;
    clickTokenRef.current += 1;
    map.easeTo({ center: [target.lng, target.lat], zoom: target.zoom ?? Math.max(map.getZoom(), 14.7), duration: 520 });
  }, [target]);

  return <div ref={hostRef} className="dense-map map" aria-label="Interactive dense Quiet LA map. Click within the modeled footprint to inspect the nearest exact receiver, or enable a context layer to inspect its locations."><div className="map-zoom" aria-label="Map zoom controls"><button type="button" onClick={() => mapRef.current?.zoomIn()} aria-label="Zoom in">+</button><button type="button" onClick={() => mapRef.current?.zoomOut()} aria-label="Zoom out">−</button><button type="button" onClick={fitCoverage} aria-label="Fit modeled coverage"><span aria-hidden="true">⤢</span></button></div></div>;
}
