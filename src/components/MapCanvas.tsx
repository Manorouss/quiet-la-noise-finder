'use client';

import { useEffect, useRef, useState } from 'react';
import type { Feature, FeatureCollection, Geometry, Point, Polygon, MultiPolygon } from 'geojson';
import type { LayerStyle, NoiseView, Period } from '@/lib/contracts';
import { fitPadding, layerDisplayAllowed, receiverValue } from '@/lib/map-state.js';

export interface MapPayload {
  four: { records: Array<{ id: string; lat: number; lng: number; relative_value: number | null; run_id: string; status: string }> };
  tarzana: { records: Array<{ receiver_id: number; lat: number; lng: number; d: number; e: number; n: number }> };
  airports: { features: Array<{ id: number; geometry: Geometry; properties: Record<string, string> }> };
  rail: { metro_routes: Array<Record<string, unknown>>; overall_status: string };
  mask: { mask_geometry_wgs84: { type: 'Polygon' | 'MultiPolygon'; coordinates: unknown }; affected_receiver_count: number; status_class: string } | null;
}

export interface InspectionRecord {
  id: string;
  label: string;
  family: string;
  region: string;
  period: Period | null;
  value: number | null;
  statusChip: string;
  evidenceClass: string;
  currentness: string;
  calibration: string;
  claimBoundary: string;
  distanceM?: number;
}

interface Props {
  payload: MapPayload | null;
  loadState: 'loading' | 'ready' | 'error';
  view: NoiseView;
  period: Period;
  layerStyle: LayerStyle;
  layerToggles: Record<string, boolean>;
  fitRequest: number;
  fitVisibleRequest: number;
  regionRequest: string;
  onInspect: (records: InspectionRecord[]) => void;
  selectedIds: string[];
  onFitComplete: () => void;
  runtimeMode: 'local' | 'private_preview' | 'external_payload_free';
}

const LA_BOUNDS: Record<string, [[number, number], [number, number]]> = {
  la: [[-118.95, 33.64], [-117.58, 34.38]],
  tarzana: [[-118.592, 34.166], [-118.576, 34.177]],
  'east-valley': [[-118.66, 34.14], [-118.08, 34.4]],
  'central-ne': [[-118.52, 33.98], [-117.88, 34.38]],
  'hollywood-westside': [[-118.56, 33.87], [-118.17, 34.2]],
};

const portalMapStyle: import('maplibre-gl').StyleSpecification = {
  version: 8 as const,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [
    { id: 'quiet-background', type: 'background', paint: { 'background-color': '#dde2df' } },
    { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-opacity': 0.82, 'raster-fade-duration': 0 } },
  ],
};

function geometryCoordinates(geometry: Geometry, output: [number, number][] = []): [number, number][] {
  if (geometry.type === 'Point') output.push(geometry.coordinates as [number, number]);
  else if (geometry.type === 'MultiPoint' || geometry.type === 'LineString') output.push(...(geometry.coordinates as [number, number][]));
  else if (geometry.type === 'MultiLineString' || geometry.type === 'Polygon') for (const part of geometry.coordinates as [number, number][][]) output.push(...part);
  else if (geometry.type === 'MultiPolygon') for (const polygon of geometry.coordinates as [number, number][][][]) for (const ring of polygon) output.push(...ring);
  else if (geometry.type === 'GeometryCollection') for (const child of geometry.geometries) geometryCoordinates(child, output);
  return output;
}

function fitVisibleBounds(map: import('maplibre-gl').Map, data: MapPayload | null, view: NoiseView, toggles: Record<string, boolean>): void {
  if (!data) return;
  const coordinates: [number, number][] = [];
  const modeled = view !== 'context';
  const context = view !== 'modeled';
  if (modeled && layerDisplayAllowed('four_region_freeway_relative') && toggles.four_region_freeway_relative) coordinates.push(...data.four.records.map((record) => [record.lng, record.lat] as [number, number]));
  if (modeled && toggles.tarzana_mixed_road_scenario) coordinates.push(...data.tarzana.records.map((record) => [record.lng, record.lat] as [number, number]));
  if (context && toggles.airport_planning_contours) for (const feature of data.airports.features) geometryCoordinates(feature.geometry, coordinates);
  if (context && toggles.source_341_incomplete_mask && data.mask) geometryCoordinates(safeMaskGeometry(data.mask), coordinates);
  if (!coordinates.length) { map.fitBounds(LA_BOUNDS.la as import('maplibre-gl').LngLatBoundsLike, { padding: 120, duration: 500 }); return; }
  const bounds: [[number, number], [number, number]] = [[Infinity, Infinity], [-Infinity, -Infinity]];
  for (const [lng, lat] of coordinates) {
    bounds[0][0] = Math.min(bounds[0][0], lng); bounds[0][1] = Math.min(bounds[0][1], lat);
    bounds[1][0] = Math.max(bounds[1][0], lng); bounds[1][1] = Math.max(bounds[1][1], lat);
  }
  const container = map.getContainer();
  map.fitBounds(bounds, { padding: fitPadding(container.clientWidth, container.clientHeight), duration: 500, maxZoom: 15 });
}

function featureCollection(features: Feature<Geometry, Record<string, unknown>>[]): FeatureCollection<Geometry> {
  return { type: 'FeatureCollection', features };
}

function visibility(enabled: boolean): 'visible' | 'none' { return enabled ? 'visible' : 'none'; }

function safeMaskGeometry(mask: NonNullable<MapPayload['mask']>): Geometry {
  return { type: mask.mask_geometry_wgs84.type, coordinates: mask.mask_geometry_wgs84.coordinates } as Polygon | MultiPolygon;
}

function buildReceiverFeatures(data: MapPayload, period: Period): Feature<Point, Record<string, unknown>>[] {
  const fourFeatures: Feature<Point, Record<string, unknown>>[] = (layerDisplayAllowed('four_region_freeway_relative') ? data.four.records : []).map((record) => ({
    type: 'Feature', id: record.id, geometry: { type: 'Point', coordinates: [record.lng, record.lat] },
    properties: { id: record.id, family: 'Four-region freeway model', region: record.run_id, period: '', value: receiverValue(record.relative_value), label: `Receiver ${record.id}`, statusChip: record.relative_value === null ? 'Incomplete — not computable' : 'Modeled — relative (uncalibrated)', evidenceClass: record.relative_value === null ? 'incomplete_not_computable' : 'modeled_relative_uncalibrated', currentness: 'not_current_or_observed', calibration: 'uncalibrated', claimBoundary: 'Modeled relative and uncalibrated; not measured dBA/CNEL; not quietness; not an address prediction.' },
  }));
  const tarzanaFeatures: Feature<Point, Record<string, unknown>>[] = data.tarzana.records.map((record) => ({
    type: 'Feature', id: `tarzana-${record.receiver_id}`, geometry: { type: 'Point', coordinates: [record.lng, record.lat] },
    properties: { id: `tarzana-${record.receiver_id}`, family: 'Tarzana mixed-road scenario', region: 'Tarzana', period, value: period === 'D' ? record.d : period === 'E' ? record.e : record.n, label: `Receiver ${record.receiver_id}`, statusChip: 'Scenario (assumed inputs)', evidenceClass: 'scenario_assumed_inputs', currentness: 'assumed_historical_context', calibration: 'uncalibrated', claimBoundary: 'Scenario with assumed temporal, fleet, and speed inputs; not current traffic; not live traffic; not observed traffic.' },
  }));
  return [...fourFeatures, ...tarzanaFeatures];
}

export default function MapCanvas({ payload, loadState, view, period, layerStyle, layerToggles, fitRequest, fitVisibleRequest, regionRequest, onInspect, selectedIds, onFitComplete, runtimeMode }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<import('maplibre-gl').Map | null>(null);
  const maplibreRef = useRef<typeof import('maplibre-gl') | null>(null);
  const dataRef = useRef<MapPayload | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [basemapUnavailable, setBasemapUnavailable] = useState(false);
  const [mapError, setMapError] = useState(false);
  const [scientificReady, setScientificReady] = useState(false);
  const lastFitVisibleRequest = useRef(0);
  const initialFitComplete = useRef(false);
  const onInspectRef = useRef(onInspect);
  onInspectRef.current = onInspect;
  dataRef.current = payload;
  const hasScientificRecords = Boolean(payload && (payload.four.records.length > 0 || payload.tarzana.records.length > 0));

  useEffect(() => {
    let cancelled = false;
    if (!hostRef.current || mapRef.current) return;
    void import('maplibre-gl').then((module) => {
      if (cancelled || !hostRef.current) return;
      maplibreRef.current = module;
      // Next bundles the main module separately; the module worker needs its
      // shared sibling served beside it from the same installed release.
      module.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');
      const map = new module.Map({ container: hostRef.current, style: portalMapStyle, attributionControl: false, center: [-118.35, 34.13], zoom: 9.25, maxZoom: 18, minZoom: 7 });
      mapRef.current = map;
      (window as unknown as { __quietMap?: typeof map }).__quietMap = map;
      map.addControl(new module.NavigationControl({ showCompass: false }), 'bottom-right');
      map.on('error', (event) => {
        const message = event.error?.message ?? '';
        if (('sourceId' in event && event.sourceId === 'osm') || /tile|raster|openstreet/i.test(message)) setBasemapUnavailable(true);
        else {
          console.error('Quiet LA map failed:', event.error);
          setMapError(true);
        }
      });
      map.on('style.load', () => {
        if (dataRef.current) installData(map, dataRef.current);
        setMapReady(true);
      });
      map.getCanvas().addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        const lngLat = map.getCenter();
        map.fire('click', { point: map.project(lngLat), lngLat });
      });
      map.on('click', (event) => {
        const layers = ['quiet-four-points', 'quiet-tarzana-points', 'quiet-airport-fill', 'quiet-mask-fill'];
        const features = map.queryRenderedFeatures(event.point, { layers: layers.filter((id) => Boolean(map.getLayer(id))) });
        const nearestByFamily = new Map<string, { feature: import('maplibre-gl').MapGeoJSONFeature; distance: number }>();
        const contextByFamily = new Map<string, import('maplibre-gl').MapGeoJSONFeature>();
        for (const feature of features) {
          const p = feature.properties ?? {};
          const family = String(p.family ?? 'context');
          if (family === 'Four-region freeway model' || family === 'Tarzana mixed-road scenario') {
            const geometry = feature.geometry;
            const coordinates = geometry.type === 'Point' ? geometry.coordinates as [number, number] : null;
            const projected = coordinates ? map.project(coordinates) : event.point;
            const distance = Math.hypot(projected.x - event.point.x, projected.y - event.point.y);
            const key = family === 'Four-region freeway model' ? 'freeway' : 'tarzana';
            if (!nearestByFamily.has(key) || distance < nearestByFamily.get(key)!.distance) nearestByFamily.set(key, { feature, distance });
          } else if (family === 'Airport planning contours') {
            if (!contextByFamily.has('airport')) contextByFamily.set('airport', feature);
          } else if (family === 'Source-341 incomplete mask') {
            contextByFamily.set('source341', feature);
          }
        }
        const selectedFeatures = [...nearestByFamily.values()].map((entry) => entry.feature).concat([...contextByFamily.values()]);
        const records: InspectionRecord[] = selectedFeatures.slice(0, 4).map((feature) => {
          const p = feature.properties ?? {};
          const distanceM = feature.geometry.type === 'Point' ? event.lngLat.distanceTo(module.LngLat.convert(feature.geometry.coordinates as [number, number])) : undefined;
          return { id: String(p.id ?? feature.id ?? 'context'), label: String(p.label ?? p.airport ?? 'Context record'), family: String(p.family ?? 'context'), region: String(p.region ?? 'Los Angeles County'), period: p.period === 'D' || p.period === 'E' || p.period === 'N' ? p.period : null, value: receiverValue(p.value), distanceM, statusChip: String(p.statusChip ?? 'Official record (context only)'), evidenceClass: String(p.evidenceClass ?? 'official_record_context_only'), currentness: String(p.currentness ?? 'not_current_or_observed'), calibration: String(p.calibration ?? 'not_applicable'), claimBoundary: String(p.claimBoundary ?? 'Separate record; no acoustic combination is admitted.'), };
        });
        onInspectRef.current(records);
      });
      map.on('mousemove', (event) => {
        const features = map.queryRenderedFeatures(event.point, { layers: ['quiet-four-points', 'quiet-tarzana-points', 'quiet-airport-fill', 'quiet-mask-fill'].filter((id) => Boolean(map.getLayer(id))) });
        map.getCanvas().style.cursor = features.length ? 'pointer' : '';
      });
    }).catch(() => { if (!cancelled) setMapError(true); });
    return () => { cancelled = true; mapRef.current?.remove(); mapRef.current = null; setMapReady(false); delete (window as unknown as { __quietMap?: unknown }).__quietMap; };
  }, []);

  function installData(map: import('maplibre-gl').Map, data: MapPayload) {
    const airportFeatures: Feature<Geometry, Record<string, unknown>>[] = data.airports.features.map((feature) => ({ type: 'Feature', id: `airport-${feature.id}`, geometry: feature.geometry, properties: { ...feature.properties, id: `airport-${feature.id}`, family: 'Airport planning contours', region: 'Los Angeles County', label: `${feature.properties.airport ?? 'Airport'} contour`, statusChip: 'Official record (context only)', evidenceClass: 'official_record_context_only', currentness: 'dated_official_context', calibration: 'not_applicable', claimBoundary: 'Official planning record shown for context only; never converted into operations, emissions, propagation, or an acoustic sum.' } }));
    const maskFeatures: Feature<Geometry, Record<string, unknown>>[] = data.mask ? [{ type: 'Feature', id: 'source-341-mask', geometry: safeMaskGeometry(data.mask), properties: { id: 'source-341-mask', family: 'Source-341 incomplete mask', region: 'Source-341 reach', label: 'Source-341 1,500 m mask', statusChip: 'Incomplete — not computable', evidenceClass: 'incomplete_not_computable', currentness: 'held_pending_correction', calibration: 'not_applicable', claimBoundary: 'This reach is incomplete — not computable for the freeway contribution; the mask is not evidence of quiet or low exposure.' } }] : [];
    const allPoints = featureCollection(buildReceiverFeatures(data, 'D'));
    if (!map.getSource('quiet-receivers')) map.addSource('quiet-receivers', { type: 'geojson', data: allPoints });
    else (map.getSource('quiet-receivers') as import('maplibre-gl').GeoJSONSource).setData(allPoints);
    if (!map.getSource('quiet-airports')) map.addSource('quiet-airports', { type: 'geojson', data: featureCollection(airportFeatures) });
    if (maskFeatures.length && !map.getSource('quiet-mask')) map.addSource('quiet-mask', { type: 'geojson', data: featureCollection(maskFeatures) });
    if (!map.getLayer('quiet-four-points')) map.addLayer({ id: 'quiet-four-points', type: 'circle', source: 'quiet-receivers', filter: ['==', ['get', 'family'], 'Four-region freeway model'], paint: { 'circle-color': ['interpolate', ['linear'], ['get', 'value'], 45, '#4f9f8d', 60, '#a8d89b', 75, '#f3e983', 90, '#f4ae54', 105, '#e46d3f', 120, '#9c2853'], 'circle-radius': 4.5, 'circle-opacity': 0.68, 'circle-stroke-color': 'rgba(255,255,255,.65)', 'circle-stroke-width': 0.5 } });
    if (!map.getLayer('quiet-tarzana-points')) map.addLayer({ id: 'quiet-tarzana-points', type: 'circle', source: 'quiet-receivers', filter: ['==', ['get', 'family'], 'Tarzana mixed-road scenario'], paint: { 'circle-color': ['interpolate', ['linear'], ['get', 'value'], 45, '#4f9f8d', 60, '#a8d89b', 75, '#f3e983', 90, '#f4ae54', 105, '#e46d3f', 120, '#9c2853'], 'circle-radius': 4, 'circle-opacity': 0.74, 'circle-stroke-color': 'rgba(255,255,255,.66)', 'circle-stroke-width': 0.5 } });
    if (!map.getLayer('quiet-airport-fill')) map.addLayer({ id: 'quiet-airport-fill', type: 'fill', source: 'quiet-airports', paint: { 'fill-color': '#75839b', 'fill-opacity': 0.11 } });
    if (!map.getLayer('quiet-airport-line')) map.addLayer({ id: 'quiet-airport-line', type: 'line', source: 'quiet-airports', paint: { 'line-color': '#65738b', 'line-width': 1.3, 'line-opacity': 0.66, 'line-dasharray': [2, 2] } });
    if (maskFeatures.length && !map.getLayer('quiet-mask-fill')) map.addLayer({ id: 'quiet-mask-fill', type: 'fill', source: 'quiet-mask', paint: { 'fill-color': '#9c2833', 'fill-opacity': 0.18 } });
    if (maskFeatures.length && !map.getLayer('quiet-mask-line')) map.addLayer({ id: 'quiet-mask-line', type: 'line', source: 'quiet-mask', paint: { 'line-color': '#9c2833', 'line-width': 2, 'line-dasharray': [1.5, 1.5], 'line-opacity': 0.9 } });
    if (!map.getLayer('quiet-selected')) map.addLayer({ id: 'quiet-selected', type: 'circle', source: 'quiet-receivers', filter: ['in', ['get', 'id'], ['literal', []]], paint: { 'circle-radius': 8, 'circle-color': 'rgba(255,255,255,0)', 'circle-stroke-color': '#171b22', 'circle-stroke-width': 2 } });
  }

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !payload) return;
    if (!map.getSource('quiet-receivers')) installData(map, payload);
    const modeled = view !== 'context';
    const context = view !== 'modeled';
    const fourOn = modeled && layerDisplayAllowed('four_region_freeway_relative') && Boolean(layerToggles.four_region_freeway_relative);
    const tarzanaOn = modeled && Boolean(layerToggles.tarzana_mixed_road_scenario);
    const airportOn = context && Boolean(layerToggles.airport_planning_contours);
    const maskOn = context && Boolean(layerToggles.source_341_incomplete_mask);
    for (const [id, on] of [['quiet-four-points', fourOn], ['quiet-tarzana-points', tarzanaOn], ['quiet-airport-fill', airportOn], ['quiet-airport-line', airportOn], ['quiet-mask-fill', maskOn], ['quiet-mask-line', maskOn]] as const) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility(on));
    const colors = layerStyle === 'dots' ? { radius: 2.1, opacity: 0.88, blur: 0 } : layerStyle === 'glow' ? { radius: 7, opacity: 0.36, blur: 0.62 } : layerStyle === 'bands' ? { radius: 6.5, opacity: 0.76, blur: 0.05 } : { radius: 4.5, opacity: 0.68, blur: 0.12 };
    for (const id of ['quiet-four-points', 'quiet-tarzana-points']) if (map.getLayer(id)) { map.setPaintProperty(id, 'circle-radius', colors.radius); map.setPaintProperty(id, 'circle-opacity', colors.opacity); map.setPaintProperty(id, 'circle-blur', colors.blur); }
  }, [mapReady, payload, view, layerStyle, layerToggles]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !payload) return;
    if (map.getSource('quiet-receivers')) {
      const source = map.getSource('quiet-receivers') as import('maplibre-gl').GeoJSONSource;
      source.setData(featureCollection(buildReceiverFeatures(payload, period)));
    }
  }, [mapReady, payload, period]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !payload) return;
    setScientificReady(false);
    const timeout = window.setTimeout(() => setMapError(true), 15000);
    const checkReady = () => {
      if (map.getSource('quiet-receivers') && map.isSourceLoaded('quiet-receivers')) {
        window.clearTimeout(timeout);
        setScientificReady(true);
      }
    };
    map.on('sourcedata', checkReady);
    checkReady();
    return () => { map.off('sourcedata', checkReady); window.clearTimeout(timeout); };
  }, [mapReady, payload]);

  useEffect(() => {
    if (mapReady && mapRef.current?.getLayer('quiet-selected')) mapRef.current.setFilter('quiet-selected', ['in', ['get', 'id'], ['literal', selectedIds]]);
  }, [mapReady, payload, selectedIds]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || (!fitRequest && initialFitComplete.current)) return;
    initialFitComplete.current = true;
    const bounds = LA_BOUNDS[regionRequest] ?? LA_BOUNDS.la;
    const container = map.getContainer();
    map.fitBounds(bounds as import('maplibre-gl').LngLatBoundsLike, { padding: fitPadding(container.clientWidth, container.clientHeight), duration: 500, maxZoom: regionRequest === 'la' ? 10.2 : 13.2 });
    onFitComplete();
  }, [fitRequest, regionRequest, mapReady, onFitComplete]);

  useEffect(() => {
    if (fitVisibleRequest && fitVisibleRequest !== lastFitVisibleRequest.current && mapReady && mapRef.current) {
      lastFitVisibleRequest.current = fitVisibleRequest;
      fitVisibleBounds(mapRef.current, payload, view, layerToggles);
    }
  }, [fitVisibleRequest, payload, view, layerToggles, mapReady]);

  const readyStatus = runtimeMode === 'external_payload_free'
    ? 'Scientific private preview is not published on this URL'
    : runtimeMode === 'private_preview'
      ? 'Scientific private preview is not yet admitted'
      : hasScientificRecords && !scientificReady
        ? 'Loading modeled receiver data…'
      : hasScientificRecords
        ? 'Interactive modeled field · OpenStreetMap basemap'
        : 'Local scientific display data is unavailable';
  return <>{mapError && <div className="load-state load-state--error glass" role="alert"><strong>The interactive map could not start.</strong><span>The map renderer or its data could not load. Reload to retry.</span><button className="load-state__action" type="button" onClick={() => window.location.reload()}>Reload map</button></div>}<div ref={hostRef} className="map" aria-label="Interactive Quiet LA MapLibre map. Use arrow keys to pan and Enter to inspect the center." /><div className="map-zoom glass" aria-label="Map zoom controls"><button type="button" onClick={() => mapRef.current?.zoomIn()} aria-label="Zoom in">+</button><button type="button" onClick={() => mapRef.current?.zoomOut()} aria-label="Zoom out">−</button><button type="button" onClick={() => { if (mapRef.current) fitVisibleBounds(mapRef.current, dataRef.current, view, layerToggles); }} aria-label="Fit currently visible layers"><span aria-hidden="true">⤢</span></button></div><div className="map-status" aria-live="polite">{mapError ? 'Map unavailable' : basemapUnavailable ? 'OpenStreetMap unavailable · paper field remains active; street orientation is unavailable' : loadState === 'ready' ? readyStatus : runtimeMode === 'local' ? 'Map awaiting accepted local display data' : 'Loading policy-aware map shell'}</div></>;
}
