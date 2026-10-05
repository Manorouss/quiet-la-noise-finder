'use client';

import { useEffect, useRef } from 'react';
import type { Map as MapLibreMap, MapGeoJSONFeature } from 'maplibre-gl';
import { buildStyle, type ContextId, type Period, type StyleOptions } from '@/lib/county-map-style';
import { createSplatLayer } from '@/lib/splat-layer';

export type Values = Record<Period, number | null>;
export type Selection =
  | { kind: 'receiver'; key: string; facade: boolean; onRoad: boolean; masked: boolean; values: Values; aircraft: number | null; building: string | null; at: [number, number] }
  | { kind: 'building'; key: string; height: number; values: Values; aircraft: number | null; count: number; at: [number, number] }
  | { kind: 'road'; name: string; aadt: number; mtfcc: string; basis: string }
  | { kind: 'context'; layer: ContextId; title: string; detail: string };
export type Camera = { lng: number; lat: number; zoom: number; pitch: number; bearing: number };
export type MapStatus = { loading: boolean; error: string | null; zoom: number };
type Props = StyleOptions & {
  target: { lng: number; lat: number; zoom?: number; pitch?: number; bearing?: number; nonce: number } | null;
  splatSceneUrl: string | null;
  onSplatStatus?: (status: 'loading' | 'ready' | 'error' | 'off') => void;
  initialCamera: Camera | null;
  selectedKey: string | null;
  onSelect: (selection: Selection | null) => void;
  onStatus: (status: MapStatus) => void;
  onCamera: (camera: Camera) => void;
};

const HOME: Camera = { lng: -118.53, lat: 34.19, zoom: 13.2, pitch: 0, bearing: 0 };
const num = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const valuesOf = (p: Record<string, unknown>): Values => ({ D: num(p.d), E: num(p.e), N: num(p.n), Q: num(p.q) });

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

function contextTitle(layer: ContextId, p: Record<string, unknown>): { title: string; detail: string } {
  if (layer === 'airport-contours') {
    const level = Number(p.CLASS);
    const band = Number.isFinite(level) ? `${level}–${level + 5} dB CNEL` : 'CNEL contour';
    return { title: `${String(p.AIRPORT_NAME || 'Airport')} · ${band}`, detail: `Official airport noise contour (${String(p.SOURCE || 'LA County Airport Land Use Plan')}). CNEL is a 24-hour average with evening and night noise weighted up; it is not yet added into the road-noise values.` };
  }
  if (layer === 'heliports') return { title: String(p.name || 'Heliport'), detail: 'Heliport location. Helicopter activity is not yet modeled.' };
  return { title: p.station ? `Fire station ${String(p.station)}` : 'Fire station', detail: `${layer === 'city-fire' ? 'City of Los Angeles' : 'LA County'} fire station. Sirens are not modeled.` };
}

export default function CountyMap(props: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const styleKey = JSON.stringify([props.layersUrl, props.period, props.noise, props.mode3d, props.roads, props.context, props.theme, props.photo3d]);

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
      const start = propsRef.current.initialCamera ?? HOME;
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
      map.on('dataloading', () => report(true));
      map.on('idle', () => report(false));
      map.on('error', (event) => { const message = event.error?.message ?? ''; if (!/tile|404|aborted/i.test(message)) report(false, message); });
      map.on('moveend', () => {
        if (!map) return;
        const c = map.getCenter();
        propsRef.current.onCamera({ lng: c.lng, lat: c.lat, zoom: map.getZoom(), pitch: map.getPitch(), bearing: map.getBearing() });
      });
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
          kind: 'building', key: String(p(f).k), height: Number(p(f).h), values: valuesOf(p(f)), aircraft: num(p(f).a), count: Number(p(f).c ?? 0), at: footprintCentre(f.geometry, clicked),
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
        } else propsRef.current.onSelect(null);
      });
      for (const id of ['receivers-dots', 'buildings-3d', 'building-footprints', 'roads-modeled', 'context-heliports', 'context-county-fire', 'context-city-fire']) {
        map.on('mouseenter', id, () => { if (map) map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', id, () => { if (map) map.getCanvas().style.cursor = ''; });
      }
    })();
    return () => { cancelled = true; map?.remove(); mapRef.current = null; };
  }, []);

  // Every setting is part of one style; setStyle diffs it into minimal map updates.
  useEffect(() => {
    const map = mapRef.current;
    if (map) map.setStyle(buildStyle(propsRef.current), { diff: true });
  }, [styleKey]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (props.mode3d && map.getPitch() < 30) map.easeTo({ pitch: 60, bearing: map.getBearing() || -17, duration: 900 });
    if (!props.mode3d && map.getPitch() > 0) map.easeTo({ pitch: 0, bearing: 0, duration: 700 });
  }, [props.mode3d]);

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
  }, [props.selectedKey, styleKey]);

  useEffect(() => {
    const map = mapRef.current;
    if (map && props.target) map.flyTo({ center: [props.target.lng, props.target.lat], zoom: props.target.zoom ?? Math.max(map.getZoom(), 15), pitch: props.target.pitch ?? map.getPitch(), bearing: props.target.bearing ?? map.getBearing(), duration: 1800, essential: true });
  }, [props.target]);

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
  }, [props.photo3d, props.splatSceneUrl, styleKey]);

  return <div ref={hostRef} className="county-map map" aria-label="Map of modeled road noise" role="region" />;
}
