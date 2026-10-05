'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Feature, FeatureCollection, Point, Polygon, Geometry } from 'geojson';
import type { ExpressionSpecification } from 'maplibre-gl';
import { resolveRuntimeProfile, scientificAssetUrl } from '@/lib/runtime-profile.js';
import { isPointInPaddedMapViewport, pilotMapPadding } from '@/lib/pilot-map-layout.js';
import { parsePilotViewHash } from '@/lib/pilot-view-state.js';
import pilotReleaseContract from '@/data/pilot-release-contract.json';
import { expectedBuildingTileIds, mergePilotTiles, normalizePilotTile, normalizeSavedBuilding, savedBuildingRecord, tileIntersectsBounds } from '@/lib/pilot-release.js';
import type { Period } from '@/lib/contracts';

type LoadState = 'loading' | 'ready' | 'error';
type Receiver = Feature<Point, { id: number; receiver_key: string; source_tile: string; masked: boolean; receiver_family: string; building_pk: number | null; building_key: string | null; height_agl_m: number; on_road?: boolean; D: Value | null; E: Value | null; N: Value | null }>;
type Value = { laeq: number | null };
type Building = Feature<Polygon, { building_pk: number; building_key: string; source_tile?: string; source_tile_ids: string[]; source_bld_id: string; height_m: number; receiver_ids: number[]; receiver_keys: string[]; receiver_count: number; periods: Record<Period, { min: number | null; max: number | null; receiver_count: number; unavailable_count: number }> }>;
type TileData = { tileId: string; receivers: Receiver[]; buildings: Building[]; manifest: Record<string, unknown> };
type PilotData = { receivers: Receiver[]; buildings: Building[]; loadedTileIds: string[]; maskedReceiverKeys: string[] };
type SavedBuilding = { buildingPk: number; buildingKey: string; sourceBldId: string; sourceTileIds: string[]; model: string };

// The county map (/map) is the main experience: a plain visit to the site root goes there before this
// pilot draws (the loading state is in the static HTML). /pilot/ and shared pilot links (#model=...,
// ?study=...) stay here.
const ROOT_TO_MAP = "if(location.pathname==='/'&&!location.hash&&!location.search)location.replace('/map/')";
const MODEL = 'tarzana-pilot-r02-c03-freeway-v1';
const STUDY_ID = pilotReleaseContract.study_id;
const DEFAULT_TILE_ID = pilotReleaseContract.default_tile_id;
const AVAILABLE_TILES = pilotReleaseContract.tiles.filter((tile) => tile.status === 'accepted_legacy_default' || tile.status === 'accepted_expansion');
const MIN_TILE_LOAD_ZOOM = 13;
const MODEL_LABELS: Record<string, string> = {
  'tarzana-pilot': 'Tarzana pilot inputs: assumed historical-context traffic on freeways and main streets.',
  'county-v1': 'County model v1: FHWA HPMS 2024 traffic counts, all Census streets, documented defaults where no count exists.',
};
function tileModel(tileId: string | undefined): string {
  const tile = pilotReleaseContract.tiles.find((candidate) => candidate.tile_id === tileId) as { model?: string } | undefined;
  return tile?.model ?? 'tarzana-pilot';
}
const COVERAGE: FeatureCollection<Polygon> = {
  type: 'FeatureCollection',
  features: AVAILABLE_TILES.map((tile) => {
    const [w, s, e, n] = tile.bbox_wgs84;
    return { type: 'Feature', properties: { tile_id: tile.tile_id }, geometry: { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] } };
  }),
};
const STORAGE_KEY = 'quiet-la-pilot-saved-buildings-v1';
const PERIODS: Period[] = ['D', 'E', 'N'];
const periodName: Record<Period, string> = { D: 'Day', E: 'Evening', N: 'Night' };
// Absolute 5 dB bands in the usual noise-map order (green below 45 dB to purple at 80+). The WHO road-traffic
// guideline (53 dB Lden, 45 dB Lnight) falls in the yellow band, so orange and above means louder than recommended.
const BAND_EDGES = [45, 50, 55, 60, 65, 70, 75, 80];
const BAND_COLORS = ['#86c58f', '#c3e19a', '#f2ec7d', '#f8c35b', '#f28f3b', '#e35a32', '#c1272d', '#8e1b4d', '#4a1a6b'];
// Masked receivers and receivers without a value for the period are grey, never a quiet color.
const bandColor = (period: Period) => ['case', ['any', ['get', 'masked'], ['==', ['get', `v${period}`], null]], '#9ea3a8',
  ['step', ['to-number', ['get', `v${period}`]], BAND_COLORS[0], ...BAND_EDGES.flatMap((edge, i) => [edge, BAND_COLORS[i + 1]])]] as unknown as ExpressionSpecification;

async function fetchAsset(path: string): Promise<unknown> {
  const profile = resolveRuntimeProfile(process.env.NEXT_PUBLIC_QUIET_LA_DATA_PROFILE);
  if (profile.id !== 'local_v3' && profile.id !== 'pilot_v1') throw new Error('The combined-road pilot is available only in the admitted local pilot profiles.');
  const response = await fetch(scientificAssetUrl(profile, `pilot/${path}`), { cache: 'no-store' });
  if (!response.ok) throw new Error(`pilot/${path} returned ${response.status}`);
  return response.json();
}

function validValue(receiver: Receiver | undefined, period: Period): number | null {
  const value = receiver?.properties?.[period]?.laeq;
  return typeof value === 'number' && Number.isFinite(value) && value !== -99 ? value : null;
}
// Map features carry all three periods (vD/vE/vN), so a period switch only changes the paint expression.
function receiverMapFeatures(receivers: Receiver[]) {
  return receivers.map((f) => ({ ...f, properties: { ...f.properties, vD: validValue(f, 'D'), vE: validValue(f, 'E'), vN: validValue(f, 'N'), id: f.properties.receiver_key } })) as Feature<Point, Record<string, unknown>>[];
}
function buildingMapFeatures(buildings: PilotData['buildings']) {
  return buildings.map((f) => ({ ...f, properties: { ...f.properties, id: f.properties.building_key } })) as Feature<Polygon, Record<string, unknown>>[];
}
function receiverStatus(receiver: Receiver) {
  if (receiver.properties.masked) return 'unavailable';
  return receiver.properties.on_road ? 'on a modeled road centerline, not a living location' : 'sampled exterior';
}
function formatRange(stats: Building['properties']['periods'][Period]) {
  if (!stats || stats.min === null || stats.max === null) return 'Unavailable';
  return `${stats.min.toFixed(1)}–${stats.max.toFixed(1)}`;
}
function featureCollection(features: Feature<Geometry, Record<string, unknown>>[]): FeatureCollection<Geometry> { return { type: 'FeatureCollection', features }; }
function bounds(features: Feature<Geometry>[]): [[number, number], [number, number]] | null {
  const points: [number, number][] = [];
  const visit = (g: Geometry) => {
    if (g.type === 'Point') points.push(g.coordinates as [number, number]);
    else if (g.type === 'Polygon') for (const ring of g.coordinates) points.push(...ring as [number, number][]);
    else if (g.type === 'MultiPolygon') for (const p of g.coordinates) for (const ring of p) points.push(...ring as [number, number][]);
  };
  features.forEach((f) => visit(f.geometry));
  if (!points.length) return null;
  return [[Math.min(...points.map((p) => p[0])), Math.min(...points.map((p) => p[1]))], [Math.max(...points.map((p) => p[0])), Math.max(...points.map((p) => p[1]))]];
}
function setHash(params: Record<string, string>) { const hash = new URLSearchParams(params).toString(); window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#${hash}`); }

export default function PilotPortal() {
  const profile = resolveRuntimeProfile(process.env.NEXT_PUBLIC_QUIET_LA_DATA_PROFILE);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [error, setError] = useState('');
  const [data, setData] = useState<PilotData | null>(null);
  const [period, setPeriod] = useState<Period>('D');
  const [mode3d, setMode3d] = useState(false);
  const [selectedBuildingPk, setSelectedBuildingPk] = useState<number | null>(null);
  const [selectedReceiverId, setSelectedReceiverId] = useState<number | null>(null);
  const [selectedBuildingKey, setSelectedBuildingKey] = useState<string | null>(null);
  const [selectedReceiverKey, setSelectedReceiverKey] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedBuilding[]>([]);
  const [tileLoadStatus, setTileLoadStatus] = useState<Record<string, 'loading' | 'error'>>({});
  const [notice, setNotice] = useState('');
  const [camera, setCamera] = useState<{ lng: number; lat: number; zoom: number } | null>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const mapRef = useRef<import('maplibre-gl').Map | null>(null);
  const mode3dRef = useRef(false);
  const dataRef = useRef<PilotData | null>(null);
  const periodRef = useRef<Period>('D');
  const selectedBuildingRef = useRef<number | null>(null);
  const selectedReceiverRef = useRef<number | null>(null);
  const selectedBuildingKeyRef = useRef<string | null>(null);
  const selectedReceiverKeyRef = useRef<string | null>(null);
  const tileDataRef = useRef<Map<string, TileData>>(new Map());
  const tileRequestsRef = useRef<Map<string, Promise<void>>>(new Map());
  const requestedSelectionTileRef = useRef<string | null>(null);
  const loadTileRef = useRef<(tileId: string) => Promise<void>>(async () => {});
  const cameraRef = useRef<{ lng: number; lat: number; zoom: number } | null>(null);
  const hashCameraActiveRef = useRef(false);
  const focusSelectionRef = useRef<(duration: number) => void>(() => {});
  const focusBuildingRef = useRef<(building: Building, duration: number) => void>(() => {});
  const pendingSavedFocusKeyRef = useRef<string | null>(null);
  const skipSelectionVisibilityRef = useRef(false);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  selectedBuildingRef.current = selectedBuildingPk; selectedReceiverRef.current = selectedReceiverId; selectedBuildingKeyRef.current = selectedBuildingKey; selectedReceiverKeyRef.current = selectedReceiverKey; cameraRef.current = camera; dataRef.current = data; periodRef.current = period;
  mode3dRef.current = mode3d;

  const receiverByKey = useMemo(() => new Map((data?.receivers ?? []).map((f) => [f.properties.receiver_key, f])), [data]);
  const receiverByLegacyId = useMemo(() => new Map((data?.receivers ?? []).filter((f) => f.properties.source_tile === DEFAULT_TILE_ID).map((f) => [f.properties.id, f])), [data]);
  const buildingByKey = useMemo(() => new Map((data?.buildings ?? []).map((f) => [f.properties.building_key, f])), [data]);
  const buildingById = useMemo(() => new Map((data?.buildings ?? []).map((f) => [f.properties.building_pk, f])), [data]);
  const selectedBuilding = selectedBuildingKey ? buildingByKey.get(selectedBuildingKey) : selectedBuildingPk === null ? undefined : buildingById.get(selectedBuildingPk);
  const selectedReceiver = selectedReceiverKey ? receiverByKey.get(selectedReceiverKey) : selectedReceiverId === null ? undefined : receiverByLegacyId.get(selectedReceiverId);
  const buildingReceivers = selectedBuilding?.properties.receiver_keys.map((key) => receiverByKey.get(key)).filter((r): r is Receiver => Boolean(r)) ?? [];
  const selectedStats = selectedBuilding?.properties.periods[period];
  const selectedBuildingExpectedTiles = useMemo(() => selectedBuilding ? expectedBuildingTileIds(selectedBuilding, pilotReleaseContract.tiles, DEFAULT_TILE_ID) : [], [selectedBuilding]);
  const selectedBuildingMissingTiles = useMemo(() => selectedBuildingExpectedTiles.filter((tileId) => !data?.loadedTileIds.includes(tileId)), [selectedBuildingExpectedTiles, data]);

  const writeView = useCallback((nextCamera?: { lng: number; lat: number; zoom: number }) => {
    const current = nextCamera ?? camera;
    const params: Record<string, string> = { model: MODEL, period };
    if (selectedBuilding?.properties.building_key) params.building_key = selectedBuilding.properties.building_key;
    if (selectedReceiver?.properties.receiver_key) params.receiver_key = selectedReceiver.properties.receiver_key;
    const requestedTile = selectedReceiver?.properties.source_tile ?? selectedBuilding?.properties.source_tile_ids.find((tileId) => tileId !== DEFAULT_TILE_ID) ?? requestedSelectionTileRef.current;
    if (requestedTile) params.tile = requestedTile;
    if (selectedBuildingPk !== null) params.building = String(selectedBuildingPk);
    if (selectedReceiverId !== null) params.receiver = String(selectedReceiverId);
    if (mode3d) params.mode = '3d';
    if (current) { params.lng = current.lng.toFixed(6); params.lat = current.lat.toFixed(6); params.z = current.zoom.toFixed(2); }
    setHash(params);
  }, [camera, mode3d, period, selectedBuildingPk, selectedReceiverId, selectedBuilding, selectedReceiver]);

  const loadTile = useCallback(async (tileId: string) => {
    if (tileDataRef.current.has(tileId)) return;
    const existing = tileRequestsRef.current.get(tileId);
    if (existing) return existing;
    const tile = AVAILABLE_TILES.find((candidate) => candidate.tile_id === tileId);
    if (!tile) throw new Error(`Study tile ${tileId} is not enabled in this build.`);
    const task = (async () => {
      const getAsset = (kind: string) => tile.assets.find((asset) => asset.kind === kind)?.path;
      const receiverPath = getAsset('receivers'); const buildingPath = getAsset('buildings'); const manifestPath = getAsset('manifest');
      if (!receiverPath || !buildingPath || !manifestPath) throw new Error(`Study tile ${tileId} has an incomplete asset contract.`);
      const [receiverDoc, buildingDoc, publicManifest] = await Promise.all([fetchAsset(receiverPath), fetchAsset(buildingPath), fetchAsset(manifestPath)]) as [{ features: Receiver[]; metadata?: Record<string, unknown> }, { features: Building[] }, Record<string, unknown>];
      const receivers = receiverDoc.features; const buildings = buildingDoc.features;
      if (receivers.length !== tile.receiver_count || buildings.length !== tile.building_count) throw new Error(`Study tile ${tileId} failed its receiver/building count contract.`);
      for (const field of ['receiver_count', 'numeric_rows', 'building_count', 'facade_receiver_count'] as const) if (publicManifest[field] !== tile[field]) throw new Error(`Study tile ${tileId} manifest count drift: ${field}`);
      if (JSON.stringify(publicManifest.masked_ids ?? []) !== JSON.stringify(tile.masked_ids)) throw new Error(`Study tile ${tileId} mask contract drift.`);
      const normalized = normalizePilotTile(tileId, receiverDoc, buildingDoc) as TileData;
      normalized.manifest = publicManifest;
      tileDataRef.current.set(tileId, normalized);
      setData(mergePilotTiles([...tileDataRef.current.values()]) as PilotData);
      setTileLoadStatus((items) => { const next = { ...items }; delete next[tileId]; return next; });
      if (tileId === DEFAULT_TILE_ID) setLoadState('ready');
    })();
    tileRequestsRef.current.set(tileId, task);
    setTileLoadStatus((items) => ({ ...items, [tileId]: 'loading' }));
    try { await task; } catch (cause) { setTileLoadStatus((items) => ({ ...items, [tileId]: 'error' })); throw cause; } finally { tileRequestsRef.current.delete(tileId); }
  }, []);
  loadTileRef.current = loadTile;

  useEffect(() => {
    let cancelled = false;
    void loadTile(DEFAULT_TILE_ID).catch((cause) => { if (!cancelled) { setError(cause instanceof Error ? cause.message : 'Pilot data could not be loaded.'); setLoadState('error'); } });
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
      if (Array.isArray(stored)) setSaved(stored.filter((x) => x?.model === MODEL || x?.model === STUDY_ID).map((x) => normalizeSavedBuilding(x, DEFAULT_TILE_ID, pilotReleaseContract.tiles.map((tile) => tile.tile_id))).filter((x): x is SavedBuilding => Boolean(x)).slice(0, 3));
    } catch { setNotice('Saved buildings could not be restored on this device.'); }
    const applyHash = () => {
      const view = parsePilotViewHash(window.location.hash, MODEL, PERIODS);
      setPeriod(view.period as Period);
      setSelectedBuildingPk(view.buildingPk);
      setSelectedReceiverId(view.receiverId);
      setSelectedBuildingKey(view.buildingKey);
      setSelectedReceiverKey(view.receiverKey ?? (view.receiverId === null ? null : `${DEFAULT_TILE_ID}:${view.receiverId}`));
      setMode3d(view.mode3d);
      setCamera(view.camera);
      selectedBuildingRef.current = view.buildingPk;
      selectedReceiverRef.current = view.receiverId;
      selectedBuildingKeyRef.current = view.buildingKey;
      selectedReceiverKeyRef.current = view.receiverKey ?? (view.receiverId === null ? null : `${DEFAULT_TILE_ID}:${view.receiverId}`);
      mode3dRef.current = view.mode3d;
      cameraRef.current = view.camera;
      hashCameraActiveRef.current = Boolean(view.camera);
      if (view.modelMismatch) setNotice('This view link belongs to a different pilot model. Choose a building to start here.');
      const requestedTile = view.tileId ?? view.receiverKey?.split(':', 1)[0] ?? null;
      requestedSelectionTileRef.current = requestedTile;
      if (requestedTile && requestedTile !== DEFAULT_TILE_ID && AVAILABLE_TILES.some((tile) => tile.tile_id === requestedTile)) {
        void loadTile(requestedTile).catch(() => setNotice(`Additional coverage tile ${requestedTile} could not be loaded.`));
      }
      const map = mapRef.current;
      if (map && view.camera) {
        const padding = pilotMapPadding(hostRef.current?.getBoundingClientRect(), panelRef.current?.getBoundingClientRect());
        map.jumpTo({ center: [view.camera.lng, view.camera.lat], zoom: view.camera.zoom, pitch: view.mode3d ? 50 : 0, bearing: view.mode3d ? -20 : 0, padding });
      }
    };
    applyHash();
    window.addEventListener('hashchange', applyHash);
    return () => { cancelled = true; window.removeEventListener('hashchange', applyHash); };
  }, [loadTile]);

  useEffect(() => { if (saved.length) localStorage.setItem(STORAGE_KEY, JSON.stringify(saved)); else localStorage.removeItem(STORAGE_KEY); }, [saved]);
  useEffect(() => {
    if (!data) return;
    const requestedReceiverKey = selectedReceiverKey ?? (selectedReceiverId === null ? null : `${DEFAULT_TILE_ID}:${selectedReceiverId}`);
    const receiver = requestedReceiverKey ? receiverByKey.get(requestedReceiverKey) : selectedReceiverId === null ? undefined : receiverByLegacyId.get(selectedReceiverId);
    if (receiver) {
      if (selectedReceiverKey !== receiver.properties.receiver_key) setSelectedReceiverKey(receiver.properties.receiver_key);
      if (selectedReceiverId !== receiver.properties.id) setSelectedReceiverId(receiver.properties.id);
      const linkedBuilding = receiver.properties.building_key ? buildingByKey.get(receiver.properties.building_key) : undefined;
      const linkedBuildingKey = linkedBuilding?.properties.building_key ?? null;
      const linkedBuildingPk = linkedBuilding?.properties.building_pk ?? null;
      if (selectedBuildingKey !== linkedBuildingKey) setSelectedBuildingKey(linkedBuildingKey);
      if (selectedBuildingPk !== linkedBuildingPk) setSelectedBuildingPk(linkedBuildingPk);
      return;
    }
    const requestedReceiverTile = requestedReceiverKey?.split(':', 1)[0];
    if ((selectedReceiverKey || selectedReceiverId !== null) && requestedReceiverTile !== DEFAULT_TILE_ID && requestedReceiverTile && tileRequestsRef.current.has(requestedReceiverTile)) return;
    if (selectedReceiverKey || selectedReceiverId !== null) {
      setSelectedReceiverKey(null); setSelectedReceiverId(null);
      setNotice('The receiver in this link is unavailable in the loaded study coverage; selection cleared.');
    }
    const requestedBuilding = selectedBuildingKey ? buildingByKey.get(selectedBuildingKey) : selectedBuildingPk === null ? undefined : buildingById.get(selectedBuildingPk);
    const requestedBuildingTile = requestedSelectionTileRef.current;
    if (!requestedBuilding && requestedBuildingTile && tileRequestsRef.current.has(requestedBuildingTile)) return;
    if (!requestedBuilding && selectedBuildingPk !== null && selectedBuildingKey === null) setNotice('The building in this link is unavailable in the loaded study coverage; selection cleared.');
    const nextBuildingKey = requestedBuilding?.properties.building_key ?? null;
    const nextBuildingPk = requestedBuilding?.properties.building_pk ?? null;
    if (selectedBuildingKey !== nextBuildingKey) setSelectedBuildingKey(nextBuildingKey);
    if (selectedBuildingPk !== nextBuildingPk) setSelectedBuildingPk(nextBuildingPk);
  }, [data, buildingById, buildingByKey, receiverByKey, receiverByLegacyId, selectedBuildingKey, selectedBuildingPk, selectedReceiverId, selectedReceiverKey]);
  useEffect(() => {
    if (!selectedBuilding) return;
    for (const tileId of selectedBuildingMissingTiles) {
      if (AVAILABLE_TILES.some((tile) => tile.tile_id === tileId)) {
        void loadTileRef.current(tileId).catch(() => setNotice(`Additional facade samples for ${selectedBuilding.properties.building_key} could not be loaded.`));
      }
    }
  }, [selectedBuilding, selectedBuildingMissingTiles]);
  useEffect(() => {
    if (!selectedBuilding || pendingSavedFocusKeyRef.current !== selectedBuilding.properties.building_key) return;
    pendingSavedFocusKeyRef.current = null;
    skipSelectionVisibilityRef.current = true;
    focusBuildingRef.current(selectedBuilding, 450);
  }, [selectedBuilding]);
  useEffect(() => { if (loadState === 'ready') writeView(); }, [loadState, period, selectedBuildingPk, selectedReceiverId, selectedBuildingKey, selectedReceiverKey, writeView]);

  const installMap = useCallback(async (pilot: PilotData) => {
    if (!hostRef.current || mapRef.current) return;
    const maplibre = await import('maplibre-gl');
    maplibre.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');
    const map = new maplibre.Map({ container: hostRef.current, style: { version: 8, sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256 } }, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#dde2df' } }, { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-opacity': 0.82, 'raster-fade-duration': 0 } }] }, center: [-118.566, 34.170], zoom: 14.2, maxZoom: 19, minZoom: 11, attributionControl: false, trackResize: false });
    mapRef.current = map;
    const viewportPadding = () => {
      const host = hostRef.current?.getBoundingClientRect();
      const panel = panelRef.current?.getBoundingClientRect();
      return pilotMapPadding(host, panel);
    };
    const keepSelectionVisible = (duration: number) => {
      if (!map.isStyleLoaded()) return;
      const padding = viewportPadding();
      const currentData = dataRef.current ?? pilot;
      const receiver = currentData.receivers.find((feature) => feature.properties.receiver_key === selectedReceiverKeyRef.current) ?? currentData.receivers.find((feature) => feature.properties.source_tile === DEFAULT_TILE_ID && feature.properties.id === selectedReceiverRef.current);
      const building = currentData.buildings.find((feature) => feature.properties.building_key === selectedBuildingKeyRef.current) ?? currentData.buildings.find((feature) => feature.properties.building_pk === selectedBuildingRef.current);
      const bb = building && bounds([building]);
      const target = receiver?.geometry.coordinates as [number, number] | undefined ?? (bb ? [(bb[0][0] + bb[1][0]) / 2, (bb[0][1] + bb[1][1]) / 2] : undefined);
      if (!target) return;
      const projected = map.project(target);
      const containerBounds = map.getContainer().getBoundingClientRect();
      if (!isPointInPaddedMapViewport(projected, { width: containerBounds.width, height: containerBounds.height }, padding)) {
        map.easeTo({ center: target, zoom: map.getZoom(), padding, duration });
      }
    };
    focusSelectionRef.current = keepSelectionVisible;
    focusBuildingRef.current = (building, duration) => {
      if (!map.isStyleLoaded()) return;
      const bb = bounds([building]);
      if (!bb) return;
      const base = viewportPadding();
      map.stop();
      map.fitBounds(bb, { padding: { top: base.top + 32, right: base.right + 32, bottom: base.bottom + 32, left: base.left + 32 }, maxZoom: 17, duration });
    };
    const resizeObserver = new ResizeObserver(() => {
      if (!map.isStyleLoaded()) return;
      const padding = viewportPadding();
      const center = map.getCenter(); const zoom = map.getZoom();
      map.resize();
      map.jumpTo({ center, zoom, padding });
      if (selectedReceiverRef.current !== null || selectedBuildingRef.current !== null) keepSelectionVisible(0);
    });
    resizeObserverRef.current = resizeObserver;
    if (hostRef.current) resizeObserver.observe(hostRef.current);
    if (panelRef.current) resizeObserver.observe(panelRef.current);
    const clearHashCamera = () => { hashCameraActiveRef.current = false; };
    map.getCanvas().addEventListener('pointerdown', clearHashCamera);
    map.getCanvas().addEventListener('wheel', clearHashCamera, { passive: true });
    map.getCanvas().addEventListener('touchstart', clearHashCamera, { passive: true });
    map.on('dragstart', clearHashCamera);
    (window as unknown as { __quietPilotMap?: typeof map }).__quietPilotMap = map;
    map.on('error', (event) => { if (/tile|raster|openstreet/i.test(event.error?.message ?? '')) return; setError(event.error?.message ?? 'Map renderer failed.'); });
    map.on('load', () => {
      map.setPadding(viewportPadding());
      const currentPilot = dataRef.current ?? pilot;
      map.addSource('pilot-receivers', { type: 'geojson', data: featureCollection(receiverMapFeatures(currentPilot.receivers)) });
      map.addSource('pilot-buildings', { type: 'geojson', data: featureCollection(buildingMapFeatures(currentPilot.buildings)) });
      map.addSource('pilot-coverage', { type: 'geojson', data: COVERAGE });
      map.addLayer({ id: 'pilot-coverage-fill', type: 'fill', source: 'pilot-coverage', paint: { 'fill-color': '#5b6b7f', 'fill-opacity': 0.04 } });
      map.addLayer({ id: 'pilot-coverage-line', type: 'line', source: 'pilot-coverage', paint: { 'line-color': '#4a5a6e', 'line-width': 1.4, 'line-opacity': 0.75, 'line-dasharray': [2, 2] } });
      map.addLayer({ id: 'pilot-buildings-fill', type: 'fill', source: 'pilot-buildings', paint: { 'fill-color': '#d8cfb9', 'fill-opacity': .30 } });
      map.addLayer({ id: 'pilot-buildings-line', type: 'line', source: 'pilot-buildings', paint: { 'line-color': '#786f5b', 'line-width': 1.1, 'line-opacity': .72 } });
      map.addLayer({ id: 'pilot-buildings-3d', type: 'fill-extrusion', source: 'pilot-buildings', layout: { visibility: mode3dRef.current ? 'visible' : 'none' }, paint: { 'fill-extrusion-color': '#a99f8b', 'fill-extrusion-base': 0, 'fill-extrusion-height': ['coalesce', ['get', 'height_m'], 4], 'fill-extrusion-opacity': .72 } });
      map.addLayer({ id: 'pilot-receivers', type: 'circle', source: 'pilot-receivers', paint: { 'circle-color': bandColor(periodRef.current), 'circle-radius': ['case', ['==', ['get', 'receiver_family'], 'building_facade_exterior'], 4.2, 2.8], 'circle-opacity': ['case', ['get', 'masked'], .55, .78], 'circle-stroke-color': 'rgba(255,255,255,.68)', 'circle-stroke-width': .6 } });
      map.addLayer({ id: 'pilot-building-selected', type: 'line', source: 'pilot-buildings', filter: ['==', ['get', 'id'], ''], paint: { 'line-color': '#171b22', 'line-width': 3.5, 'line-opacity': .95 } });
      map.addLayer({ id: 'pilot-receiver-selected', type: 'circle', source: 'pilot-receivers', filter: ['==', ['get', 'id'], ''], paint: { 'circle-color': 'rgba(255,255,255,0)', 'circle-stroke-color': '#171b22', 'circle-stroke-width': 2.2, 'circle-radius': 8 } });
      map.setFilter('pilot-building-selected', ['==', ['get', 'id'], selectedBuildingKeyRef.current ?? '']);
      map.setFilter('pilot-receiver-selected', ['==', ['get', 'id'], selectedReceiverKeyRef.current ?? '']);
      const initialPadding = viewportPadding();
      const allBounds = bounds([...currentPilot.buildings, ...currentPilot.receivers]); if (allBounds) map.fitBounds(allBounds, { padding: { top: initialPadding.top + 56, right: initialPadding.right + 56, bottom: initialPadding.bottom + 56, left: initialPadding.left + 56 }, duration: 0, maxZoom: 15.5 });
      const hashCam = cameraRef.current; if (hashCam) map.jumpTo({ center: [hashCam.lng, hashCam.lat], zoom: hashCam.zoom, pitch: mode3dRef.current ? 50 : 0, bearing: mode3dRef.current ? -20 : 0, padding: initialPadding });
      else if (mode3dRef.current) map.jumpTo({ pitch: 50, bearing: -20 });
      else if (selectedReceiverRef.current !== null || selectedBuildingRef.current !== null) keepSelectionVisible(0);
      const loadVisibleTiles = () => {
        if (map.getZoom() < MIN_TILE_LOAD_ZOOM) return;
        const currentBounds = map.getBounds();
        for (const tile of AVAILABLE_TILES) if (tile.tile_id !== DEFAULT_TILE_ID && tileIntersectsBounds(tile.bbox_wgs84, currentBounds)) {
          void loadTileRef.current(tile.tile_id).catch(() => setNotice(`Additional coverage tile ${tile.tile_id} could not be loaded.`));
        }
      };
      map.on('moveend', loadVisibleTiles);
      loadVisibleTiles();
    });
    map.on('click', (event) => {
      const hit = map.queryRenderedFeatures(event.point, { layers: ['pilot-receiver-selected', 'pilot-receivers', 'pilot-buildings-3d', 'pilot-buildings-fill'] });
      const receiver = hit.find((f) => f.layer.id.includes('receiver'));
      if (receiver) { const key = String(receiver.properties?.id ?? ''); const r = dataRef.current?.receivers.find((x) => x.properties.receiver_key === key); if (r) { hashCameraActiveRef.current = false; requestedSelectionTileRef.current = null; setSelectedReceiverKey(key); setSelectedReceiverId(r.properties.id); const linkedBuilding = r.properties.building_key ? dataRef.current?.buildings.find((b) => b.properties.building_key === r.properties.building_key) : undefined; setSelectedBuildingKey(linkedBuilding?.properties.building_key ?? null); setSelectedBuildingPk(linkedBuilding?.properties.building_pk ?? null); return; } }
      const building = hit.find((f) => f.layer.id === 'pilot-buildings-fill' || f.layer.id === 'pilot-buildings-3d'); const key = String(building?.properties?.building_key ?? building?.properties?.id ?? ''); const selected = dataRef.current?.buildings.find((b) => b.properties.building_key === key); if (selected) { hashCameraActiveRef.current = false; requestedSelectionTileRef.current = null; setSelectedBuildingKey(key); setSelectedBuildingPk(selected.properties.building_pk); setSelectedReceiverId(null); setSelectedReceiverKey(null); }
    });
    map.on('moveend', () => { const c = map.getCenter(); setCamera({ lng: c.lng, lat: c.lat, zoom: map.getZoom() }); });
  }, []);

  useEffect(() => { if (data) void installMap(data); }, [data, installMap]);
  // Each effect touches only what changed: re-sending every receiver to the map worker on a click or a
  // period switch made the map stall. Not gated on isStyleLoaded(), which is false while basemap tiles
  // load; an update skipped then was never retried, leaving newly loaded tiles off the map.
  const whenMapReady = useCallback((apply: (map: import('maplibre-gl').Map) => void) => {
    const map = mapRef.current; if (!map) return;
    if (map.getSource('pilot-receivers')) apply(map); else map.once('load', () => apply(map));
  }, []);
  useEffect(() => {
    if (!data) return;
    whenMapReady((map) => {
      (map.getSource('pilot-receivers') as import('maplibre-gl').GeoJSONSource).setData(featureCollection(receiverMapFeatures(data.receivers)));
      (map.getSource('pilot-buildings') as import('maplibre-gl').GeoJSONSource).setData(featureCollection(buildingMapFeatures(data.buildings)));
    });
  }, [data, whenMapReady]);
  useEffect(() => {
    whenMapReady((map) => { if (map.getLayer('pilot-receivers')) map.setPaintProperty('pilot-receivers', 'circle-color', bandColor(period)); });
  }, [period, whenMapReady]);
  useEffect(() => {
    whenMapReady((map) => {
      if (map.getLayer('pilot-building-selected')) map.setFilter('pilot-building-selected', ['==', ['get', 'id'], selectedBuilding?.properties.building_key ?? '']);
      if (map.getLayer('pilot-receiver-selected')) map.setFilter('pilot-receiver-selected', ['==', ['get', 'id'], selectedReceiver?.properties.receiver_key ?? '']);
    });
  }, [selectedBuilding, selectedReceiver, whenMapReady]);
  useEffect(() => {
    if (skipSelectionVisibilityRef.current) { skipSelectionVisibilityRef.current = false; return; }
    if (mapRef.current && !hashCameraActiveRef.current && (selectedBuildingPk !== null || selectedReceiverId !== null)) focusSelectionRef.current(250);
  }, [selectedBuildingPk, selectedBuildingKey, selectedReceiverId]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (map.getLayer('pilot-buildings-3d')) map.setLayoutProperty('pilot-buildings-3d', 'visibility', mode3d ? 'visible' : 'none');
    map.easeTo({ pitch: mode3d ? 50 : 0, bearing: mode3d ? -20 : 0, duration: 350 });
  }, [mode3d]);
  useEffect(() => () => { resizeObserverRef.current?.disconnect(); resizeObserverRef.current = null; mapRef.current?.remove(); mapRef.current = null; delete (window as unknown as { __quietPilotMap?: unknown }).__quietPilotMap; }, []);

  const selectBuilding = (key: string) => { hashCameraActiveRef.current = false; requestedSelectionTileRef.current = null; const b = buildingByKey.get(key); setSelectedBuildingKey(key); setSelectedBuildingPk(b?.properties.building_pk ?? null); setSelectedReceiverId(null); setSelectedReceiverKey(null); const bb = b && bounds([b]); if (bb && mapRef.current) { mapRef.current.stop(); const base = pilotMapPadding(hostRef.current?.getBoundingClientRect(), panelRef.current?.getBoundingClientRect()); const padding = { top: base.top + 32, right: base.right + 32, bottom: base.bottom + 32, left: base.left + 32 }; mapRef.current.fitBounds(bb, { padding, maxZoom: 17, duration: 450 }); } };
  const requestSavedBuilding = (item: SavedBuilding) => {
    hashCameraActiveRef.current = false;
    const loadedBuilding = buildingByKey.get(item.buildingKey);
    pendingSavedFocusKeyRef.current = loadedBuilding ? null : item.buildingKey;
    if (loadedBuilding) skipSelectionVisibilityRef.current = true;
    const expectedTiles = expectedBuildingTileIds({ properties: { source_bld_id: item.sourceBldId, source_tile_ids: item.sourceTileIds } }, pilotReleaseContract.tiles, DEFAULT_TILE_ID);
    requestedSelectionTileRef.current = expectedTiles.find((tileId) => tileId !== DEFAULT_TILE_ID) ?? DEFAULT_TILE_ID;
    setSelectedBuildingKey(item.buildingKey);
    setSelectedBuildingPk(item.buildingPk);
    setSelectedReceiverId(null); setSelectedReceiverKey(null);
    if (loadedBuilding) focusBuildingRef.current(loadedBuilding, 450);
    let unavailable = false;
    for (const tileId of expectedTiles) {
      if (dataRef.current?.loadedTileIds.includes(tileId)) continue;
      if (AVAILABLE_TILES.some((tile) => tile.tile_id === tileId)) void loadTileRef.current(tileId).catch(() => setNotice(`Saved-building tile ${tileId} could not be loaded; no acoustic value was substituted.`));
      else unavailable = true;
    }
    if (unavailable) setNotice('Some saved-building coverage is not included in this release. The displayed range uses loaded tiles only.');
  };
  const requestSavedCoverage = () => {
    for (const item of saved) {
      const building = buildingByKey.get(item.buildingKey);
      const expectedTiles = building ? expectedBuildingTileIds(building, pilotReleaseContract.tiles, DEFAULT_TILE_ID) : expectedBuildingTileIds({ properties: { source_bld_id: item.sourceBldId, source_tile_ids: item.sourceTileIds } }, pilotReleaseContract.tiles, DEFAULT_TILE_ID);
      for (const tileId of expectedTiles) {
      if (!dataRef.current?.loadedTileIds.includes(tileId) && AVAILABLE_TILES.some((tile) => tile.tile_id === tileId)) {
        void loadTileRef.current(tileId).catch(() => setNotice(`Saved-building tile ${tileId} could not be loaded; it remains unavailable.`));
      }
      }
    }
  };
  const toggleSave = () => {
    if (!selectedBuilding) return;
    if (saved.some((x) => x.buildingKey === selectedBuilding.properties.building_key)) setSaved((items) => items.filter((x) => x.buildingKey !== selectedBuilding.properties.building_key));
    else if (saved.length >= 3) setNotice('Comparison is limited to three buildings. Remove one to save another.');
    else { setSaved((items) => [...items, savedBuildingRecord(selectedBuilding, MODEL)]); setNotice('Building saved on this device.'); }
  };
  const copyView = async () => { writeView(); try { await navigator.clipboard.writeText(window.location.href); setNotice('View link copied.'); } catch { setNotice('View state is in the address bar. Copy that link.'); } };
  const selectedReceiverValue = validValue(selectedReceiver, period);

  if (loadState === 'loading') return <main className="pilot-shell"><script dangerouslySetInnerHTML={{ __html: ROOT_TO_MAP }} /><div className="pilot-state" role="status">Loading Los Angeles road-noise results…</div></main>;
  if (loadState === 'error') return <main className="pilot-shell"><div className="pilot-state pilot-state--error" role="alert"><strong>The pilot could not start.</strong><span>{error}</span><button type="button" onClick={() => window.location.reload()}>Reload</button></div></main>;
  return <main className="pilot-shell" aria-labelledby="pilot-title">
    <div ref={hostRef} className="pilot-map" aria-label="Combined-road pilot map. Building footprints and sampled exterior receivers." />
    <header className="pilot-brand glass"><div className="brand-lockup"><span className="brand-mark" aria-hidden="true">QL</span><div><h1 id="pilot-title">Quiet LA</h1><p>Building preview</p></div></div><span className="internal-badge">{profile.badge}</span><a className="pilot-new-map" href="/map/">New: county map →</a></header>
    <aside ref={panelRef} className="pilot-panel glass" aria-label="Combined-road pilot controls">
      <div className="pilot-heading"><div><span className="eyebrow">Los Angeles · combined roads</span><h2>Exterior road noise</h2></div><span className="pilot-back">{data?.buildings.length ?? 0} buildings</span></div>
      <p className="pilot-intro">Modeled exterior exposure for {data?.buildings.length ?? 0} buildings across {data?.loadedTileIds.length ?? 0} loaded coverage tile{data?.loadedTileIds.length === 1 ? '' : 's'}, sampled at 4 m height. Freeway and local roads under an assumed traffic scenario.</p>
      <div className="pilot-periods" role="radiogroup" aria-label="Scenario period">{PERIODS.map((p) => <button key={p} type="button" role="radio" aria-checked={period === p} className={period === p ? 'is-active' : ''} onClick={() => setPeriod(p)}><b>{p}</b><small>{periodName[p]}</small></button>)}</div>
      <div className="pilot-actions"><button type="button" aria-pressed={mode3d} onClick={() => setMode3d((enabled) => !enabled)}>{mode3d ? '3D view on' : '3D view'}</button><button type="button" onClick={copyView}>Copy view link</button>{selectedBuilding && <button type="button" onClick={toggleSave}>{saved.some((x) => x.buildingPk === selectedBuildingPk) ? 'Saved building' : 'Save building'}</button>}</div>
      {notice && <p className="pilot-notice" role="status">{notice}</p>}
      {selectedBuilding ? <section className="pilot-detail" aria-live="polite"><div className="pilot-detail-heading"><div><span className="eyebrow">Selected building</span><h3>{selectedBuilding.properties.building_pk}</h3><p>Source building ID {selectedBuilding.properties.source_bld_id}</p><p className="pilot-model-note">{MODEL_LABELS[tileModel(selectedBuilding.properties.source_tile ?? selectedBuilding.properties.source_tile_ids[0])] ?? ''}</p></div><button type="button" onClick={() => { hashCameraActiveRef.current = false; setSelectedBuildingPk(null); setSelectedBuildingKey(null); setSelectedReceiverId(null); setSelectedReceiverKey(null); }} aria-label="Clear building selection">×</button></div><div className="pilot-range"><strong>{formatRange(selectedStats!)}</strong><span>{periodName[period]} exterior road LAEQ range</span></div><dl className="pilot-stats"><div><dt>Exterior samples</dt><dd>{selectedStats?.receiver_count ?? 0}</dd></div><div><dt>Unavailable</dt><dd>{selectedStats?.unavailable_count ?? 0}</dd></div><div><dt>Height</dt><dd>4 m AGL</dd></div></dl>{selectedBuildingMissingTiles.length > 0 && <p className="pilot-boundary">Partial tile coverage: samples from {selectedBuildingExpectedTiles.length - selectedBuildingMissingTiles.length} of {selectedBuildingExpectedTiles.length} tiles are included. {selectedBuildingMissingTiles.some((tileId) => AVAILABLE_TILES.some((tile) => tile.tile_id === tileId)) ? 'Loading the adjacent tile…' : 'The adjacent tile is not included in this release.'}</p>}{selectedBuilding.properties.height_m < 4 && buildingReceivers.some((r) => r.properties.receiver_family === 'building_facade_exterior' && r.properties.height_agl_m === 4) && <p className="pilot-boundary">Some 4 m facade samples are above the mapped building height ({selectedBuilding.properties.height_m.toFixed(2)} m).</p>}<p className="pilot-boundary">Sampled exterior range at 4 m; this is not a whole-property, interior, apartment, floor-specific, measurement, or guarantee value.</p>{selectedReceiver ? <div className="pilot-receiver-focus"><strong>Receiver {selectedReceiver.properties.id}</strong><span>{selectedReceiverValue === null ? 'Unavailable' : `${selectedReceiverValue.toFixed(1)} LAEQ`}</span><small>{periodName[period]} · {selectedReceiver.properties.height_agl_m} m AGL · {receiverStatus(selectedReceiver)}</small></div> : <p className="pilot-receiver-hint">Select an exterior receiver point to inspect its individual value.</p>}<details open className="pilot-receiver-list"><summary>Exterior receivers ({buildingReceivers.length})</summary><div>{buildingReceivers.map((r) => <button key={r.properties.receiver_key} type="button" className={selectedReceiverKey === r.properties.receiver_key ? 'is-selected' : ''} onClick={() => { hashCameraActiveRef.current = false; requestedSelectionTileRef.current = null; setSelectedReceiverKey(r.properties.receiver_key); setSelectedReceiverId(r.properties.id); const linked = r.properties.building_key ? buildingByKey.get(r.properties.building_key) : undefined; setSelectedBuildingKey(linked?.properties.building_key ?? null); setSelectedBuildingPk(linked?.properties.building_pk ?? null); }}><span>{r.properties.id}</span><strong>{validValue(r, period) === null ? 'Unavailable' : validValue(r, period)!.toFixed(1)}</strong></button>)}</div></details></section> : selectedReceiver ? <section className="pilot-detail pilot-receiver-only" aria-live="polite"><div className="pilot-detail-heading"><div><span className="eyebrow">Selected receiver</span><h3>{selectedReceiver.properties.id}</h3></div><button type="button" onClick={() => { hashCameraActiveRef.current = false; setSelectedReceiverId(null); setSelectedReceiverKey(null); }} aria-label="Clear receiver selection">×</button></div><div className="pilot-receiver-focus"><strong>{selectedReceiverValue === null ? 'Unavailable' : `${selectedReceiverValue.toFixed(1)} LAEQ`}</strong><small>{periodName[period]} · {selectedReceiver.properties.height_agl_m} m AGL · {receiverStatus(selectedReceiver)}</small></div><p className="pilot-boundary">Individual sampled exterior receiver; no building association is available for this point.</p><p className="pilot-model-note">{MODEL_LABELS[tileModel(selectedReceiver.properties.source_tile)] ?? ''}</p></section> : <section className="pilot-building-list"><div className="pilot-list-heading"><strong>Select a building</strong><span>{saved.length} / 3 saved</span></div><p>Choose a footprint or use this keyboard-accessible list.</p><div className="pilot-list">{(data?.buildings ?? []).map((b) => <button key={b.properties.building_key} type="button" onClick={() => selectBuilding(b.properties.building_key)}><span>Building {b.properties.building_pk}</span><small>{b.properties.receiver_count} exterior samples</small></button>)}</div></section>}
      <details className="pilot-compare" onToggle={(event) => { if (event.currentTarget.open) requestSavedCoverage(); }}><summary>Saved comparison <span>{saved.length} / 3</span></summary>{saved.length === 0 ? <p>Save up to three buildings to compare exterior ranges for the selected period.</p> : <div>{saved.map((item) => { const b = buildingByKey.get(item.buildingKey); const stats = b?.properties.periods[period]; const expectedTiles = b ? expectedBuildingTileIds(b, pilotReleaseContract.tiles, DEFAULT_TILE_ID) : expectedBuildingTileIds({ properties: { source_bld_id: item.sourceBldId, source_tile_ids: item.sourceTileIds } }, pilotReleaseContract.tiles, DEFAULT_TILE_ID); const pendingTile = expectedTiles.find((tileId) => !data?.loadedTileIds.includes(tileId)); const coverageStatus = pendingTile ? (tileLoadStatus[pendingTile] === 'loading' ? `loading ${pendingTile}` : tileLoadStatus[pendingTile] === 'error' ? `${pendingTile} failed to load` : AVAILABLE_TILES.some((tile) => tile.tile_id === pendingTile) ? `loading ${pendingTile}` : `${pendingTile} not included`) : ''; const rowStatus = b ? `${formatRange(stats!)} · ${periodName[period]}${coverageStatus ? ` · partial: ${coverageStatus}` : ''}` : pendingTile ? (tileLoadStatus[pendingTile] === 'loading' ? `Loading ${pendingTile}…` : tileLoadStatus[pendingTile] === 'error' ? `${pendingTile} failed to load` : AVAILABLE_TILES.some((tile) => tile.tile_id === pendingTile) ? `${pendingTile} will load when selected` : `${pendingTile} coverage not included`) : 'Building unavailable in loaded coverage'; return <div className="pilot-compare-row" key={item.buildingKey}><button type="button" onClick={() => requestSavedBuilding(item)}>Building {b?.properties.building_pk ?? item.buildingPk}<small>{rowStatus}</small></button><button type="button" aria-label={`Remove building ${item.buildingPk}`} onClick={() => setSaved((items) => items.filter((x) => x.buildingKey !== item.buildingKey))}>×</button></div>; })}</div>}</details>
      <details className="pilot-disclosure"><summary>Coverage & limits</summary><p>Combined freeway and local-road exterior LAeq for day, evening, and night. Current loaded coverage includes {data?.receivers.length.toLocaleString()} receivers, {data?.buildings.reduce((sum, b) => sum + b.properties.receiver_count, 0).toLocaleString()} linked exterior samples, and {data?.buildings.length} building footprints. Coverage loads as you pan; counts can increase. {data?.maskedReceiverKeys.length ?? 0} receivers are unavailable and shown in grey because they failed a calculation or physical plausibility check. Points within 3 m of a modeled road centerline are labelled as on the road; they are not living locations.</p><p>This is an assumed traffic scenario informed by historical context. Its activity assumptions do not describe observed street conditions: evening flow is set to 0.6× day and night to 0.2× day; 113 official road segments have no assigned activity in this preview. Reflection order is zero. Sound bends over roofs and terrain; bending around the sides of buildings is not modeled, as the CNOSSOS-EU method specifies for road traffic. Results come from two road models: the Tarzana pilot assumed-traffic scenario, and County model v1, which uses FHWA HPMS 2024 traffic counts where they exist, includes every Census street down to residential roads, and uses documented defaults elsewhere. Each selected building or point names its model. Areas outside the dashed outlines have not been modeled yet; they are not quiet. The modeled exterior LAeq values are uncalibrated and are not measurements, indoor or apartment levels, or an acoustic accuracy certification. The optional 3D view displays building geometry and is not an acoustic volume.</p><p>Building footprints and linked geometry © LARIAC, County of Los Angeles, Pictometry, EagleView. Local-road geometry: LA County DPW StreetMap Primary/Secondary. Freeway geometry and traffic context: California Department of Transportation (Caltrans). Basemap © OpenStreetMap contributors. LA County data is informational and carries no warranty or County endorsement. See <a href="https://egis-lacounty.hub.arcgis.com/pages/terms-of-use" target="_blank" rel="noreferrer">County terms</a> and <a href="https://dot.ca.gov/conditions-of-use" target="_blank" rel="noreferrer">Caltrans Conditions of Use</a>.</p></details>
    </aside>
    <div className="pilot-legend glass"><strong>Road noise · {periodName[period]} · dB LAeq</strong><div className="pilot-legend-bar" aria-hidden="true">{BAND_COLORS.map((color) => <i key={color} style={{ background: color }} />)}</div><div className="pilot-legend-ticks" aria-hidden="true">{BAND_EDGES.map((edge) => <span key={edge}>{edge}</span>)}</div><small>Exterior, modeled, uncalibrated. WHO guideline for road traffic: 53 dB Lden, 45 dB at night.</small><a className="pilot-attribution" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a></div>
    <div className="pilot-status" role="status">{data?.receivers.length.toLocaleString()} receivers · {data?.buildings.length} buildings · {data?.loadedTileIds.length} loaded tiles · {periodName[period]}{camera && camera.zoom < MIN_TILE_LOAD_ZOOM ? ' · Zoom in to load results inside the dashed outlines' : ''}</div>
  </main>;
}
