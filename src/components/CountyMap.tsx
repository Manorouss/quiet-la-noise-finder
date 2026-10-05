'use client';

import { useEffect, useRef, useState } from 'react';
import type { Map as MapLibreMap, MapGeoJSONFeature } from 'maplibre-gl';
import { buildStyle, isHospitalPad, terrainFor, type ContextId, type Period, type StyleOptions } from '@/lib/county-map-style';
import { drawIcon } from '@/lib/map-icons';
import { createSplatLayer } from '@/lib/splat-layer';

export type Values = Record<Period, number | null>;
export type Selection =
  | { kind: 'receiver'; key: string; facade: boolean; onRoad: boolean; masked: boolean; values: Values; aircraft: number | null; building: string | null; at: [number, number] }
  | { kind: 'building'; key: string; height: number; values: Values; lowest: Values; aircraft: number | null; count: number; at: [number, number]; address?: string }
  | { kind: 'road'; name: string; aadt: number; mtfcc: string; basis: string }
  | { kind: 'context'; layer: ContextId; title: string; detail: string }
  | { kind: 'empty'; at: [number, number] };  // nothing modeled under the click or search
export type Camera = { lng: number; lat: number; zoom: number; pitch: number; bearing: number };
export type MapStatus = { loading: boolean; error: string | null; zoom: number };
type Props = StyleOptions & {
  // select: after the flight, select the building at (or nearest to) the point, e.g. a searched address.
  target: { lng: number; lat: number; zoom?: number; pitch?: number; bearing?: number; nonce: number; select?: boolean; label?: string } | null;
  splatSceneUrl: string | null;
  onSplatStatus?: (status: 'loading' | 'ready' | 'error' | 'off') => void;
  initialCamera: Camera | null;
  selectedKey: string | null;
  selectedAt?: [number, number] | null;  // phones: keep the selected place visible above the bottom sheet
  onSelect: (selection: Selection | null) => void;
  onStatus: (status: MapStatus) => void;
  onCamera: (camera: Camera) => void;
};

const HOME: Camera = { lng: -118.53, lat: 34.19, zoom: 13.2, pitch: 0, bearing: 0 };
const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const valuesOf = (p: Record<string, unknown>): Values => ({ D: num(p.d), E: num(p.e), N: num(p.n), Q: num(p.q) });
const lowestOf = (p: Record<string, unknown>): Values => ({ D: num(p.dl), E: num(p.el), N: num(p.nl), Q: num(p.ql) });

/** Area centroid of a footprint's largest ring (lng/lat), where its address point most likely sits. */
function footprintCentre(geometry: GeoJSON.Geometry, fallback: [number, number]): [number, number] {
  const rings = geometry.type === 'Polygon' ? [geometry.coordinates[0]] : geometry.type === 'MultiPolygon' ? geometry.coordinates.map((poly) => poly[0]) : [];
  let best: [number, number] = fallback;
  let bestArea = 0;
  for (const ring of rings) {
    let area = 0, cx = 0, cy = 0;
    for (let i = 0; i < ring.length - 1; i += 1) {
      const [x0, y0] = ring[i], [x1, y1] = ring[i + 1];
      const cross = x0 * y1 - x1 * y0;
      area += cross; cx += (x0 + x1) * cross; cy += (y0 + y1) * cross;
    }
    if (Math.abs(area) > bestArea) { bestArea = Math.abs(area); best = [cx / (3 * area), cy / (3 * area)]; }
  }
  return best;
}

/** "4957 MELROSE AVE." -> "4957 Melrose Ave."; mixed-case text is left alone. */
function titleCase(text: string) {
  if (text !== text.toUpperCase()) return text.trim();
  return text.trim().toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase());
}

function contextTitle(layer: ContextId, p: Record<string, unknown>): { title: string; detail: string } {
  if (layer === 'airport-contours') {
    const level = Number(p.CLASS);
    const band = Number.isFinite(level) ? `${level}–${level + 5} dB CNEL` : 'CNEL contour';
    return { title: `${String(p.AIRPORT_NAME || 'Airport')} · ${band}`, detail: `Official airport noise contour (${String(p.SOURCE || 'LA County Airport Land Use Plan')}). CNEL is a 24-hour average with evening and night noise weighted up; it is not yet added into the road-noise values.` };
  }
  if (layer === 'heliports') {
    const where = titleCase(String(p.city || ''));
    const hospital = isHospitalPad(p.name);
    return { title: titleCase(String(p.name || 'Heliport')), detail: [
      hospital ? 'Hospital helipad: medical helicopters may land here at any hour.' : 'Heliport or helistop. Many rooftop pads in LA are for emergencies and see few flights.',
      where ? `In ${where}.` : '', 'Helicopter noise is not in the map values.',
    ].filter(Boolean).join(' ') };
  }
  const address = [titleCase(String(p.address || '')), titleCase(String(p.city || ''))].filter(Boolean).join(', ');
  return { title: p.station ? `Fire station ${String(p.station)}` : 'Fire station', detail: [
    `${layer === 'city-fire' ? 'LA City Fire Department' : 'LA County Fire Department'}${address ? `, ${address}` : ''}.`,
    'Engines leave with sirens, mostly along the main streets nearby. Sirens are not in the map values.',
  ].join(' ') };
}

/** Select the building at or nearest to a point (within ~25 m), e.g. a searched address or a shared link. */
function selectNear(map: MapLibreMap, lng: number, lat: number, onSelect: (selection: Selection | null) => void, address?: string) {
  const layers = ['buildings-3d', 'building-footprints'].filter((id) => map.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none');
  const point = map.project([lng, lat]);
  const metres = 40075016 * Math.cos((lat * Math.PI) / 180) / (512 * 2 ** map.getZoom());
  const pad = Math.max(12, 25 / metres);
  const hits = map.queryRenderedFeatures([[point.x - pad, point.y - pad], [point.x + pad, point.y + pad]], { layers });
  const inside = map.queryRenderedFeatures(point, { layers });
  const pickFrom = inside.length ? inside : hits;
  const best = pickFrom.map((f) => {
    const [cx, cy] = footprintCentre(f.geometry, [lng, lat]);
    const p = map.project([cx, cy]);
    return { f, d: Math.hypot(p.x - point.x, p.y - point.y) };
  }).sort((a, b) => a.d - b.d)[0]?.f;
  if (!best) { onSelect({ kind: 'empty', at: [lng, lat] }); return; }
  const p = best.properties as Record<string, unknown>;
  // Keep the building's own centre (a shared link then finds its parcel); show the searched address if any.
  onSelect({ kind: 'building', key: String(p.k), height: Number(p.h), values: valuesOf(p), lowest: lowestOf(p), aircraft: num(p.a), count: Number(p.c ?? 0), at: footprintCentre(best.geometry, [lng, lat]), address });
}

export default function CountyMap(props: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  // 2D <-> 3D builds a fresh map at the same camera: turning terrain on in a running MapLibre 6.9 map
  // left it blank (headless Chrome), while a map created with terrain works. mapVersion re-runs the
  // effects that attach things to the map (selection highlight, Photo 3D layer).
  const [mapVersion, setMapVersion] = useState(0);
  const lastCameraRef = useRef<Camera | null>(null);
  const styleKey = JSON.stringify([props.layersUrl, props.period, props.noise, props.mode3d, props.roads, props.context, props.theme, props.photo3d, props.roadField]);

  useEffect(() => {
    let cancelled = false;
    let map: MapLibreMap | null = null;
    void (async () => {
      const [maplibre, { Protocol }] = await Promise.all([import('maplibre-gl'), import('pmtiles')]);
      if (cancelled || !hostRef.current) return;
      maplibre.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');
      if (!(globalThis as { __quietPmtiles?: boolean }).__quietPmtiles) {
        maplibre.addProtocol('pmtiles', new Protocol({ metadata: true }).tile);
        (globalThis as { __quietPmtiles?: boolean }).__quietPmtiles = true;
      }
      const saved = lastCameraRef.current ?? propsRef.current.initialCamera ?? HOME;
      const three = propsRef.current.mode3d;
      const start = { ...saved, pitch: three ? (saved.pitch >= 30 ? saved.pitch : 60) : 0, bearing: three ? (saved.bearing || -17) : 0 };
      map = new maplibre.Map({
        container: hostRef.current, style: buildStyle(propsRef.current), center: [start.lng, start.lat], zoom: start.zoom, pitch: start.pitch, bearing: start.bearing,
        minZoom: 9, maxZoom: 19, maxPitch: 78, attributionControl: { compact: true }, canvasContextAttributes: { antialias: true },
      });
      mapRef.current = map;
      (window as { __quietCountyMap?: MapLibreMap }).__quietCountyMap = map;
      map.addControl(new maplibre.NavigationControl({ visualizePitch: true }), 'bottom-right');
      map.addControl(new maplibre.GeolocateControl({ trackUserLocation: false }), 'bottom-right');
      map.addControl(new maplibre.ScaleControl({ unit: 'imperial' }), 'bottom-right');
      const report = (loading: boolean, error: string | null = null) => propsRef.current.onStatus({ loading, error, zoom: map?.getZoom() ?? 0 });
      // "Loading" covers the first load of each map only: later tile loads (panning, distant 3D terrain
      // that keeps streaming in) do not bring the notice back.
      let settled = false;
      const settle = () => { settled = true; report(false); };
      map.on('styleimagemissing', (event) => {
        const icon = map && !map.hasImage(event.id) ? drawIcon(event.id) : null;
        if (icon) map?.addImage(event.id, icon, { pixelRatio: 2 });
      });
      map.on('dataloading', () => { if (!settled) report(true); });
      map.on('idle', settle);
      map.once('load', settle);  // first complete render; in 3D, idle can take long while terrain streams
      map.on('error', (event) => { const message = event.error?.message ?? ''; if (!/tile|404|aborted/i.test(message)) report(false, message); });
      map.on('moveend', () => {
        if (!map) return;
        const c = map.getCenter();
        const camera = { lng: c.lng, lat: c.lat, zoom: map.getZoom(), pitch: map.getPitch(), bearing: map.getBearing() };
        lastCameraRef.current = camera;
        propsRef.current.onCamera(camera);
      });
      map.once('load', () => { if (!cancelled) setMapVersion((v) => v + 1); });
      map.on('click', (event) => {
        if (!map) return;
        const pad = 8;
        const box: [[number, number], [number, number]] = [[event.point.x - pad, event.point.y - pad], [event.point.x + pad, event.point.y + pad]];
        const layers = ['receivers-dots', 'receivers-hit', 'buildings-3d', 'building-footprints', 'roads-modeled', 'context-heliports', 'context-county-fire', 'context-city-fire', 'context-airport-contours']
          .filter((id) => map?.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none');
        const hits = map.queryRenderedFeatures(box, { layers });
        const nearest = (features: MapGeoJSONFeature[]) => features.map((f) => {
          const [lng, lat] = (f.geometry as GeoJSON.Point).coordinates;
          const p = map!.project([lng, lat]);
          return { f, d: Math.hypot(p.x - event.point.x, p.y - event.point.y) };
        }).sort((a, b) => a.d - b.d)[0]?.f;
        const receiver = nearest(hits.filter((f) => f.layer.id.startsWith('receivers')));
        const building = hits.find((f) => f.layer.id === 'buildings-3d' || f.layer.id === 'building-footprints');
        const road = hits.find((f) => f.layer.id === 'roads-modeled');
        const context = hits.find((f) => f.layer.id.startsWith('context-'));
        const p = (f: MapGeoJSONFeature) => f.properties as Record<string, unknown>;
        const clicked: [number, number] = [event.lngLat.lng, event.lngLat.lat];
        const selectBuilding = (f: MapGeoJSONFeature) => propsRef.current.onSelect({
          kind: 'building', key: String(p(f).k), height: Number(p(f).h), values: valuesOf(p(f)), lowest: lowestOf(p(f)), aircraft: num(p(f).a), count: Number(p(f).c ?? 0), at: footprintCentre(f.geometry, clicked),
        });
        if (propsRef.current.mode3d && building) {
          selectBuilding(building);
        } else if (receiver) {
          const r = p(receiver);
          const at = (receiver.geometry as GeoJSON.Point).coordinates as [number, number];
          propsRef.current.onSelect({ kind: 'receiver', key: String(r.k), facade: r.f === 1, onRoad: r.o === 1, masked: r.m === 1, values: valuesOf(r), aircraft: num(r.a), building: r.b ? String(r.b) : null, at: [at[0], at[1]] });
        } else if (building) {
          selectBuilding(building);
        } else if (road) {
          propsRef.current.onSelect({ kind: 'road', name: String(p(road).nm || 'Unnamed road'), aadt: Number(p(road).a), mtfcc: String(p(road).c), basis: String(p(road).t) });
        } else if (context) {
          const layer = context.layer.id.replace(/^context-/, '').replace(/-line$/, '') as ContextId;
          propsRef.current.onSelect({ kind: 'context', layer, ...contextTitle(layer, p(context)) });
        } else propsRef.current.onSelect({ kind: 'empty', at: clicked });
      });
      for (const id of ['receivers-dots', 'buildings-3d', 'building-footprints', 'roads-modeled', 'context-heliports', 'context-county-fire', 'context-city-fire']) {
        map.on('mouseenter', id, () => { if (map) map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', id, () => { if (map) map.getCanvas().style.cursor = ''; });
      }
    })();
    return () => {
      cancelled = true;
      if (map) {
        const c = map.getCenter();
        lastCameraRef.current = { lng: c.lng, lat: c.lat, zoom: map.getZoom(), pitch: map.getPitch(), bearing: map.getBearing() };
        map.remove();
      }
      mapRef.current = null;
    };
  }, [props.mode3d]);

  // Every setting is part of one style; setStyle diffs it into minimal map updates. Terrain is kept out of
  // the diff and set directly (see terrainFor in county-map-style).
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const next = buildStyle(propsRef.current);
    next.terrain = map.getStyle()?.terrain;
    map.setStyle(next, { diff: true });
    const apply = () => {
      const terrain = terrainFor(propsRef.current) ?? null;
      if (JSON.stringify(map.getTerrain() ?? null) !== JSON.stringify(terrain)) map.setTerrain(terrain);
    };
    try { apply(); } catch { map.once('styledata', () => { try { apply(); } catch { /* next style change retries */ } }); }
  }, [styleKey]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      const key = props.selectedKey ?? '';
      // Layer ids are integers (older builds used strings): compare as text.
      if (map.getLayer('selected-point')) map.setFilter('selected-point', ['==', ['to-string', ['get', 'k']], key]);
      if (map.getLayer('selected-building')) map.setFilter('selected-building', ['==', ['to-string', ['get', 'k']], key]);
    };
    if (map.isStyleLoaded()) apply(); else map.once('idle', apply);
  }, [props.selectedKey, styleKey, mapVersion]);

  // On phones the panel grows when a place is selected; if that hides the place, ease it above the sheet.
  const revealKey = props.selectedAt ? `${props.selectedAt[0]},${props.selectedAt[1]}` : '';
  useEffect(() => {
    const map = mapRef.current;
    const at = propsRef.current.selectedAt;
    if (!map || !at || window.innerWidth > 720) return;
    const frame = window.requestAnimationFrame(() => {
      const panel = document.querySelector('.map-guide')?.getBoundingClientRect();
      const host = hostRef.current?.getBoundingClientRect();
      if (!panel || !host) return;
      const y = map.project(at).y + host.top;
      if (y > panel.top - 24 || y < host.top + 24) {
        map.easeTo({ center: at, padding: { top: 0, left: 0, right: 0, bottom: Math.max(0, host.bottom - panel.top) }, duration: 600 });
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [revealKey]);

  // A target set before the map exists (a shared link with a selected place) runs once the map loads;
  // each target runs once, so rebuilding the map for 2D/3D does not fly back to it.
  const handledTargetRef = useRef(0);
  useEffect(() => {
    const map = mapRef.current;
    const target = props.target;
    if (!map || !target || handledTargetRef.current === target.nonce) return;
    handledTargetRef.current = target.nonce;
    // On phones the bottom sheet covers the lower map: centre the place in the part above it.
    const panel = document.querySelector('.map-guide')?.getBoundingClientRect();
    const padding = panel && window.innerWidth <= 720 ? { top: 0, right: 0, left: 0, bottom: Math.min(panel.height, window.innerHeight * 0.6) } : undefined;
    map.flyTo({ center: [target.lng, target.lat], zoom: target.zoom ?? Math.max(map.getZoom(), 15), pitch: target.pitch ?? map.getPitch(), bearing: target.bearing ?? map.getBearing(), padding, duration: 1800, essential: true });
    if (!target.select) return;
    let done = false;
    const pick = () => {
      if (done || !mapRef.current) return;
      done = true;
      selectNear(mapRef.current, target.lng, target.lat, propsRef.current.onSelect, target.label);
    };
    // After the flight, once its tiles are drawn (or after 6 s at the latest).
    const onEnd = () => { map.once('idle', pick); };
    map.once('moveend', onEnd);
    // Fallback if idle never comes; a flight paused in a background tab keeps waiting for moveend.
    const timer = window.setTimeout(() => { if (!map.isMoving()) pick(); }, 6000);
    return () => { done = true; window.clearTimeout(timer); map.off('moveend', onEnd); map.off('idle', pick); };
  }, [props.target, mapVersion]);

  // Photo 3D showcase: a Gaussian-splat scene in a custom layer (re-added after style changes,
  // which drop custom layers).
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;
    const want = Boolean(props.photo3d && props.splatSceneUrl);
    const sync = async () => {
      const has = Boolean(map.getLayer('splat-scene'));
      if (!want) {
        if (has) map.removeLayer('splat-scene');
        propsRef.current.onSplatStatus?.('off');
        return;
      }
      if (has) return;
      propsRef.current.onSplatStatus?.('loading');
      try {
        const meta = await (await fetch(`${props.splatSceneUrl}.json`)).json();
        const layer = await createSplatLayer(map, { url: `${props.splatSceneUrl}.spz`, origin: meta.origin_lonlat, originZ: meta.origin_z_m, label: 'showcase' }, () => propsRef.current.onSplatStatus?.('ready'));
        if (cancelled || map.getLayer('splat-scene')) return;
        const firstLabel = map.getStyle().layers.find((l) => l.type === 'symbol')?.id;
        map.addLayer(layer, firstLabel);
      } catch {
        propsRef.current.onSplatStatus?.('error');
      }
    };
    if (map.isStyleLoaded()) void sync(); else map.once('idle', () => void sync());
    return () => { cancelled = true; };
  }, [props.photo3d, props.splatSceneUrl, styleKey, mapVersion]);

  return <div ref={hostRef} className="county-map map" aria-label="Map of modeled road noise" role="region" />;
}
