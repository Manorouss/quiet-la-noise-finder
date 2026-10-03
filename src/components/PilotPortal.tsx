'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Feature, FeatureCollection, Point, Polygon, Geometry } from 'geojson';
import { resolveRuntimeProfile, scientificAssetUrl } from '@/lib/runtime-profile.js';
import { resolvePilotSelection } from '@/lib/pilot-selection.js';
import { isPointInPaddedMapViewport, pilotMapPadding } from '@/lib/pilot-map-layout.js';
import { parsePilotViewHash } from '@/lib/pilot-view-state.js';
import type { Period } from '@/lib/contracts';

type LoadState = 'loading' | 'ready' | 'error';
type Receiver = Feature<Point, { id: number; masked: boolean; receiver_family: string; building_pk: number | null; height_agl_m: number; D: Value; E: Value; N: Value }>;
type Value = { laeq: number | null };
type Building = Feature<Polygon, { building_pk: number; source_bld_id: string; height_m: number; receiver_ids: number[]; receiver_count: number; periods: Record<Period, { min: number | null; max: number | null; receiver_count: number; unavailable_count: number }> }>;
type PilotData = { receivers: Receiver[]; buildings: Building[]; manifest: Record<string, unknown> };
type SavedBuilding = { buildingPk: number; sourceBldId: string; model: string };

const MODEL = 'tarzana-pilot-r02-c03-freeway-v1';
const STORAGE_KEY = 'quiet-la-pilot-saved-buildings-v1';
const PERIODS: Period[] = ['D', 'E', 'N'];
const periodName: Record<Period, string> = { D: 'Day', E: 'Evening', N: 'Night' };
const ramp = ['#4f9f8d', '#a8d89b', '#f3e983', '#f4ae54', '#e46d3f', '#9c2853'];

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
  const [saved, setSaved] = useState<SavedBuilding[]>([]);
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
  const cameraRef = useRef<{ lng: number; lat: number; zoom: number } | null>(null);
  const hashCameraActiveRef = useRef(false);
  const focusSelectionRef = useRef<(duration: number) => void>(() => {});
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  selectedBuildingRef.current = selectedBuildingPk; selectedReceiverRef.current = selectedReceiverId; cameraRef.current = camera; dataRef.current = data; periodRef.current = period;
  mode3dRef.current = mode3d;

  const receiverById = useMemo(() => new Map((data?.receivers ?? []).map((f) => [f.properties.id, f])), [data]);
  const buildingById = useMemo(() => new Map((data?.buildings ?? []).map((f) => [f.properties.building_pk, f])), [data]);
  const selectedBuilding = selectedBuildingPk === null ? undefined : buildingById.get(selectedBuildingPk);
  const selectedReceiver = selectedReceiverId === null ? undefined : receiverById.get(selectedReceiverId);
  const buildingReceivers = selectedBuilding?.properties.receiver_ids.map((id) => receiverById.get(id)).filter((r): r is Receiver => Boolean(r)) ?? [];
  const selectedStats = selectedBuilding?.properties.periods[period];

  const writeView = useCallback((nextCamera?: { lng: number; lat: number; zoom: number }) => {
    const current = nextCamera ?? camera;
    const params: Record<string, string> = { model: MODEL, period };
    if (selectedBuildingPk !== null) params.building = String(selectedBuildingPk);
    if (selectedReceiverId !== null) params.receiver = String(selectedReceiverId);
    if (mode3d) params.mode = '3d';
    if (current) { params.lng = current.lng.toFixed(6); params.lat = current.lat.toFixed(6); params.z = current.zoom.toFixed(2); }
    setHash(params);
  }, [camera, mode3d, period, selectedBuildingPk, selectedReceiverId]);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([fetchAsset('benchmark.geojson'), fetchAsset('buildings.geojson'), fetchAsset('build-manifest.json')]).then(([receivers, buildings, manifest]) => {
      if (cancelled) return;
      const receiverFeatures = (receivers as { features: Receiver[] }).features;
      const buildingFeatures = (buildings as { features: Building[] }).features;
      if (receiverFeatures.length !== 6421 || buildingFeatures.length !== 80) throw new Error('Pilot asset count contract failed.');
      setData({ receivers: receiverFeatures, buildings: buildingFeatures, manifest: manifest as Record<string, unknown> });
      setLoadState('ready');
    }).catch((cause) => { if (!cancelled) { setError(cause instanceof Error ? cause.message : 'Pilot data could not be loaded.'); setLoadState('error'); } });
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
      if (Array.isArray(stored)) setSaved(stored.filter((x) => x?.model === MODEL && Number.isInteger(x.buildingPk)).slice(0, 3));
    } catch { setNotice('Saved buildings could not be restored on this device.'); }
    const applyHash = () => {
      const view = parsePilotViewHash(window.location.hash, MODEL, PERIODS);
      setPeriod(view.period as Period);
      setSelectedBuildingPk(view.buildingPk);
      setSelectedReceiverId(view.receiverId);
      setMode3d(view.mode3d);
      setCamera(view.camera);
      selectedBuildingRef.current = view.buildingPk;
      selectedReceiverRef.current = view.receiverId;
      mode3dRef.current = view.mode3d;
      cameraRef.current = view.camera;
      hashCameraActiveRef.current = Boolean(view.camera);
      if (view.modelMismatch) setNotice('This view link belongs to a different pilot model. Choose a building to start here.');
      const map = mapRef.current;
      if (map && view.camera) {
        const padding = pilotMapPadding(hostRef.current?.getBoundingClientRect(), panelRef.current?.getBoundingClientRect());
        map.jumpTo({ center: [view.camera.lng, view.camera.lat], zoom: view.camera.zoom, pitch: view.mode3d ? 50 : 0, bearing: view.mode3d ? -20 : 0, padding });
      }
    };
    applyHash();
    window.addEventListener('hashchange', applyHash);
    return () => { cancelled = true; window.removeEventListener('hashchange', applyHash); };
  }, []);

  useEffect(() => { if (saved.length) localStorage.setItem(STORAGE_KEY, JSON.stringify(saved)); else localStorage.removeItem(STORAGE_KEY); }, [saved]);
  useEffect(() => {
    if (!data) return;
    const selection = resolvePilotSelection(selectedReceiverId, selectedBuildingPk, receiverById, buildingById);
    if (selection.receiverId !== selectedReceiverId) { setSelectedReceiverId(null); setNotice('The receiver in this link is unavailable in this pilot; selection cleared.'); }
    if (selection.buildingPk !== selectedBuildingPk) {
      setSelectedBuildingPk(selection.buildingPk);
      if (selectedBuildingPk !== null && !buildingById.has(selectedBuildingPk)) setNotice('The building in this link is no longer in the 80-building pilot; selection cleared.');
    }
  }, [data, buildingById, receiverById, selectedBuildingPk, selectedReceiverId]);
  useEffect(() => { if (loadState === 'ready') writeView(); }, [loadState, period, selectedBuildingPk, selectedReceiverId, writeView]);

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
      const receiver = pilot.receivers.find((feature) => feature.properties.id === selectedReceiverRef.current);
      const building = pilot.buildings.find((feature) => feature.properties.building_pk === selectedBuildingRef.current);
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
      const receiverFeatures = pilot.receivers.map((f) => ({ ...f, properties: { ...f.properties, value: validValue(f, periodRef.current), id: String(f.properties.id) } })) as Feature<Point, Record<string, unknown>>[];
      const buildings = pilot.buildings.map((f) => ({ ...f, properties: { ...f.properties, id: String(f.properties.building_pk) } })) as Feature<Polygon, Record<string, unknown>>[];
      map.addSource('pilot-receivers', { type: 'geojson', data: featureCollection(receiverFeatures) });
      map.addSource('pilot-buildings', { type: 'geojson', data: featureCollection(buildings) });
      map.addLayer({ id: 'pilot-buildings-fill', type: 'fill', source: 'pilot-buildings', paint: { 'fill-color': '#d8cfb9', 'fill-opacity': .30 } });
      map.addLayer({ id: 'pilot-buildings-line', type: 'line', source: 'pilot-buildings', paint: { 'line-color': '#786f5b', 'line-width': 1.1, 'line-opacity': .72 } });
      map.addLayer({ id: 'pilot-buildings-3d', type: 'fill-extrusion', source: 'pilot-buildings', layout: { visibility: mode3dRef.current ? 'visible' : 'none' }, paint: { 'fill-extrusion-color': '#a99f8b', 'fill-extrusion-base': 0, 'fill-extrusion-height': ['coalesce', ['get', 'height_m'], 4], 'fill-extrusion-opacity': .72 } });
      map.addLayer({ id: 'pilot-receivers', type: 'circle', source: 'pilot-receivers', paint: { 'circle-color': ['case', ['get', 'masked'], '#9ea3a8', ['interpolate', ['linear'], ['get', 'value'], 45, ramp[0], 60, ramp[1], 75, ramp[2], 90, ramp[3], 105, ramp[4], 120, ramp[5]]], 'circle-radius': ['case', ['==', ['get', 'receiver_family'], 'building_facade_exterior'], 4.2, 2.8], 'circle-opacity': ['case', ['get', 'masked'], .55, .78], 'circle-stroke-color': 'rgba(255,255,255,.68)', 'circle-stroke-width': .6 } });
      map.addLayer({ id: 'pilot-building-selected', type: 'line', source: 'pilot-buildings', filter: ['==', ['get', 'id'], ''], paint: { 'line-color': '#171b22', 'line-width': 3.5, 'line-opacity': .95 } });
      map.addLayer({ id: 'pilot-receiver-selected', type: 'circle', source: 'pilot-receivers', filter: ['==', ['get', 'id'], ''], paint: { 'circle-color': 'rgba(255,255,255,0)', 'circle-stroke-color': '#171b22', 'circle-stroke-width': 2.2, 'circle-radius': 8 } });
      map.setFilter('pilot-building-selected', ['==', ['get', 'id'], selectedBuildingRef.current === null ? '' : String(selectedBuildingRef.current)]);
      map.setFilter('pilot-receiver-selected', ['==', ['get', 'id'], selectedReceiverRef.current === null ? '' : String(selectedReceiverRef.current)]);
      const initialPadding = viewportPadding();
      const allBounds = bounds([...pilot.buildings, ...pilot.receivers]); if (allBounds) map.fitBounds(allBounds, { padding: { top: initialPadding.top + 56, right: initialPadding.right + 56, bottom: initialPadding.bottom + 56, left: initialPadding.left + 56 }, duration: 0, maxZoom: 15.5 });
      const hashCam = cameraRef.current; if (hashCam) map.jumpTo({ center: [hashCam.lng, hashCam.lat], zoom: hashCam.zoom, pitch: mode3dRef.current ? 50 : 0, bearing: mode3dRef.current ? -20 : 0, padding: initialPadding });
      else if (mode3dRef.current) map.jumpTo({ pitch: 50, bearing: -20 });
      else if (selectedReceiverRef.current !== null || selectedBuildingRef.current !== null) keepSelectionVisible(0);
    });
    map.on('click', (event) => {
      const hit = map.queryRenderedFeatures(event.point, { layers: ['pilot-receiver-selected', 'pilot-receivers', 'pilot-buildings-3d', 'pilot-buildings-fill'] });
      const receiver = hit.find((f) => f.layer.id.includes('receiver'));
      if (receiver) { const id = Number(receiver.properties?.id); if (Number.isInteger(id)) { hashCameraActiveRef.current = false; const r = dataRef.current?.receivers.find((x) => x.properties.id === id); setSelectedReceiverId(id); const linkedBuilding = r?.properties.building_pk ?? null; setSelectedBuildingPk(linkedBuilding); return; } }
      const building = hit.find((f) => f.layer.id === 'pilot-buildings-fill' || f.layer.id === 'pilot-buildings-3d'); const id = Number(building?.properties?.building_pk ?? building?.properties?.id); if (Number.isInteger(id)) { hashCameraActiveRef.current = false; setSelectedBuildingPk(id); setSelectedReceiverId(null); }
    });
    map.on('moveend', () => { const c = map.getCenter(); setCamera({ lng: c.lng, lat: c.lat, zoom: map.getZoom() }); });
  }, []);

  useEffect(() => { if (data) void installMap(data); }, [data, installMap]);
  useEffect(() => {
    const map = mapRef.current; if (!map || !map.isStyleLoaded()) return;
    const periodValue = data?.receivers.map((f) => ({ ...f, properties: { ...f.properties, value: validValue(f, period), id: String(f.properties.id) } })) as Feature<Point, Record<string, unknown>>[] | undefined;
    if (periodValue && map.getSource('pilot-receivers')) (map.getSource('pilot-receivers') as import('maplibre-gl').GeoJSONSource).setData(featureCollection(periodValue));
    if (map.getLayer('pilot-building-selected')) map.setFilter('pilot-building-selected', ['==', ['get', 'id'], selectedBuildingPk === null ? '' : String(selectedBuildingPk)]);
    if (map.getLayer('pilot-receiver-selected')) map.setFilter('pilot-receiver-selected', ['==', ['get', 'id'], selectedReceiverId === null ? '' : String(selectedReceiverId)]);
  }, [data, period, selectedBuildingPk, selectedReceiverId]);
  useEffect(() => {
    if (mapRef.current && !hashCameraActiveRef.current && (selectedBuildingPk !== null || selectedReceiverId !== null)) focusSelectionRef.current(250);
  }, [selectedBuildingPk, selectedReceiverId]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (map.getLayer('pilot-buildings-3d')) map.setLayoutProperty('pilot-buildings-3d', 'visibility', mode3d ? 'visible' : 'none');
    map.easeTo({ pitch: mode3d ? 50 : 0, bearing: mode3d ? -20 : 0, duration: 350 });
  }, [mode3d]);
  useEffect(() => () => { resizeObserverRef.current?.disconnect(); resizeObserverRef.current = null; mapRef.current?.remove(); mapRef.current = null; delete (window as unknown as { __quietPilotMap?: unknown }).__quietPilotMap; }, []);

  const selectBuilding = (id: number) => { hashCameraActiveRef.current = false; setSelectedBuildingPk(id); setSelectedReceiverId(null); const b = buildingById.get(id); const bb = b && bounds([b]); if (bb && mapRef.current) { mapRef.current.stop(); const base = pilotMapPadding(hostRef.current?.getBoundingClientRect(), panelRef.current?.getBoundingClientRect()); const padding = { top: base.top + 32, right: base.right + 32, bottom: base.bottom + 32, left: base.left + 32 }; mapRef.current.fitBounds(bb, { padding, maxZoom: 17, duration: 450 }); } };
  const toggleSave = () => {
    if (!selectedBuilding) return;
    if (saved.some((x) => x.buildingPk === selectedBuilding.properties.building_pk)) setSaved((items) => items.filter((x) => x.buildingPk !== selectedBuilding.properties.building_pk));
    else if (saved.length >= 3) setNotice('Comparison is limited to three buildings. Remove one to save another.');
    else { setSaved((items) => [...items, { buildingPk: selectedBuilding.properties.building_pk, sourceBldId: selectedBuilding.properties.source_bld_id, model: MODEL }]); setNotice('Building saved on this device.'); }
  };
  const copyView = async () => { writeView(); try { await navigator.clipboard.writeText(window.location.href); setNotice('View link copied.'); } catch { setNotice('View state is in the address bar. Copy that link.'); } };
  const selectedReceiverValue = validValue(selectedReceiver, period);

  if (loadState === 'loading') return <main className="pilot-shell"><div className="pilot-state" role="status">Loading the Tarzana combined-road pilot · 80 modeled buildings…</div></main>;
  if (loadState === 'error') return <main className="pilot-shell"><div className="pilot-state pilot-state--error" role="alert"><strong>The pilot could not start.</strong><span>{error}</span><button type="button" onClick={() => window.location.reload()}>Reload</button></div></main>;
  return <main className="pilot-shell" aria-labelledby="pilot-title">
    <div ref={hostRef} className="pilot-map" aria-label="Combined-road pilot map. Building footprints and sampled exterior receivers." />
    <header className="pilot-brand glass"><div className="brand-lockup"><span className="brand-mark" aria-hidden="true">QL</span><div><h1 id="pilot-title">Quiet LA</h1><p>Building preview</p></div></div><span className="internal-badge">{profile.badge}</span></header>
    <aside ref={panelRef} className="pilot-panel glass" aria-label="Combined-road pilot controls">
      <div className="pilot-heading"><div><span className="eyebrow">Tarzana · combined roads</span><h2>Exterior road noise</h2></div><span className="pilot-back">80 buildings</span></div>
      <p className="pilot-intro">Modeled exterior exposure for 80 buildings, sampled at 4 m height. Freeway and local roads under an assumed traffic scenario.</p>
      <div className="pilot-periods" role="radiogroup" aria-label="Scenario period">{PERIODS.map((p) => <button key={p} type="button" role="radio" aria-checked={period === p} className={period === p ? 'is-active' : ''} onClick={() => setPeriod(p)}><b>{p}</b><small>{periodName[p]}</small></button>)}</div>
      <div className="pilot-actions"><button type="button" aria-pressed={mode3d} onClick={() => setMode3d((enabled) => !enabled)}>{mode3d ? '3D view on' : '3D view'}</button><button type="button" onClick={copyView}>Copy view link</button>{selectedBuilding && <button type="button" onClick={toggleSave}>{saved.some((x) => x.buildingPk === selectedBuildingPk) ? 'Saved building' : 'Save building'}</button>}</div>
      {notice && <p className="pilot-notice" role="status">{notice}</p>}
      {selectedBuilding ? <section className="pilot-detail" aria-live="polite"><div className="pilot-detail-heading"><div><span className="eyebrow">Selected building</span><h3>{selectedBuilding.properties.building_pk}</h3><p>Source building ID {selectedBuilding.properties.source_bld_id}</p></div><button type="button" onClick={() => { hashCameraActiveRef.current = false; setSelectedBuildingPk(null); setSelectedReceiverId(null); }} aria-label="Clear building selection">×</button></div><div className="pilot-range"><strong>{formatRange(selectedStats!)}</strong><span>{periodName[period]} exterior road LAEQ range</span></div><dl className="pilot-stats"><div><dt>Exterior samples</dt><dd>{selectedStats?.receiver_count ?? 0}</dd></div><div><dt>Unavailable</dt><dd>{selectedStats?.unavailable_count ?? 0}</dd></div><div><dt>Height</dt><dd>4 m AGL</dd></div></dl><p className="pilot-boundary">Sampled exterior range at 4 m; this is not a whole-property, interior, apartment, floor-specific, measurement, or guarantee value.</p>{selectedReceiver ? <div className="pilot-receiver-focus"><strong>Receiver {selectedReceiver.properties.id}</strong><span>{selectedReceiverValue === null ? 'Unavailable' : `${selectedReceiverValue.toFixed(1)} LAEQ`}</span><small>{periodName[period]} · {selectedReceiver.properties.height_agl_m} m AGL · {selectedReceiver.properties.masked ? 'masked' : 'sampled exterior'}</small></div> : <p className="pilot-receiver-hint">Select an exterior receiver point to inspect its individual value.</p>}<details open className="pilot-receiver-list"><summary>Exterior receivers ({buildingReceivers.length})</summary><div>{buildingReceivers.map((r) => <button key={r.properties.id} type="button" className={selectedReceiverId === r.properties.id ? 'is-selected' : ''} onClick={() => { hashCameraActiveRef.current = false; setSelectedReceiverId(r.properties.id); setSelectedBuildingPk(r.properties.building_pk ?? null); }}><span>{r.properties.id}</span><strong>{validValue(r, period) === null ? 'Unavailable' : validValue(r, period)!.toFixed(1)}</strong></button>)}</div></details></section> : selectedReceiver ? <section className="pilot-detail pilot-receiver-only" aria-live="polite"><div className="pilot-detail-heading"><div><span className="eyebrow">Selected receiver</span><h3>{selectedReceiver.properties.id}</h3></div><button type="button" onClick={() => { hashCameraActiveRef.current = false; setSelectedReceiverId(null); }} aria-label="Clear receiver selection">×</button></div><div className="pilot-receiver-focus"><strong>{selectedReceiverValue === null ? 'Unavailable' : `${selectedReceiverValue.toFixed(1)} LAEQ`}</strong><small>{periodName[period]} · {selectedReceiver.properties.height_agl_m} m AGL · {selectedReceiver.properties.masked ? 'masked' : 'sampled exterior'}</small></div><p className="pilot-boundary">Individual sampled exterior receiver; no building association is available for this point.</p></section> : <section className="pilot-building-list"><div className="pilot-list-heading"><strong>Select a building</strong><span>{saved.length} / 3 saved</span></div><p>Choose a footprint or use this keyboard-accessible list.</p><div className="pilot-list">{(data?.buildings ?? []).map((b) => <button key={b.properties.building_pk} type="button" onClick={() => selectBuilding(b.properties.building_pk)}><span>Building {b.properties.building_pk}</span><small>{b.properties.receiver_count} exterior samples</small></button>)}</div></section>}
      <details className="pilot-compare"><summary>Saved comparison <span>{saved.length} / 3</span></summary>{saved.length === 0 ? <p>Save up to three buildings to compare exterior ranges for the selected period.</p> : <div>{saved.map((item) => { const b = buildingById.get(item.buildingPk); const stats = b?.properties.periods[period]; return <div className="pilot-compare-row" key={item.buildingPk}><button type="button" onClick={() => selectBuilding(item.buildingPk)}>Building {item.buildingPk}<small>{formatRange(stats!)} · {periodName[period]}</small></button><button type="button" aria-label={`Remove building ${item.buildingPk}`} onClick={() => setSaved((items) => items.filter((x) => x.buildingPk !== item.buildingPk))}>×</button></div>; })}</div>}</details>
      <details className="pilot-disclosure"><summary>Coverage & limits</summary><p>Combined freeway and local-road exterior LAeq for day, evening, and night: 6,421 receivers, including 1,573 façade samples linked to 80 building footprints. Receivers 85715 and 88627 are unavailable in all three periods and remain masked.</p><p>This is an assumed traffic scenario informed by historical context. Its activity assumptions do not describe observed street conditions: evening flow is set to 0.6× day and night to 0.2× day; 113 official road segments have no assigned activity in this preview. Reflection order is zero. The modeled exterior LAeq values are uncalibrated and are not measurements, indoor or apartment levels, or an acoustic accuracy certification. The optional 3D view displays building geometry and is not an acoustic volume.</p><p>Building footprints and linked geometry © LARIAC, County of Los Angeles, Pictometry, EagleView. Local-road geometry: LA County DPW StreetMap Primary/Secondary. Freeway geometry and traffic context: California Department of Transportation (Caltrans). Basemap © OpenStreetMap contributors. LA County data is informational and carries no warranty or County endorsement. See <a href="https://egis-lacounty.hub.arcgis.com/pages/terms-of-use" target="_blank" rel="noreferrer">County terms</a> and <a href="https://dot.ca.gov/conditions-of-use" target="_blank" rel="noreferrer">Caltrans Conditions of Use</a>.</p></details>
    </aside>
    <div className="pilot-legend glass"><span>lower</span><i aria-hidden="true" /><span>higher</span><small>Combined-road LAEQ · sampled exterior points</small><a className="pilot-attribution" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a></div>
    <div className="pilot-status" role="status">{data?.receivers.length.toLocaleString()} receivers · {data?.buildings.length} buildings · {periodName[period]}</div>
  </main>;
}
