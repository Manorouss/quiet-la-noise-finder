import { layers as basemapLayers, namedFlavor } from '@protomaps/basemaps';
import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from 'maplibre-gl';

export type Period = 'D' | 'E' | 'N' | 'Q';  // Q: 24 h CNEL, roads + aircraft
export type NoiseStyle = 'field' | 'bands' | 'glow' | 'dots';
export type BaseTheme = 'light' | 'dark' | 'grayscale' | 'satellite';
export type ContextId = 'airport-contours' | 'heliports' | 'county-fire' | 'city-fire';
export type StyleOptions = { layersUrl: string; period: Period; noise: NoiseStyle; mode3d: boolean; roads: boolean; context: Record<ContextId, boolean>; theme: BaseTheme; photo3d?: boolean };

// Absolute 5 dB bands, the same scale as the pilot page. The WHO road-traffic guideline
// (53 dB Lden, 45 dB Lnight) falls in the yellow band.
export const BAND_EDGES = [45, 50, 55, 60, 65, 70, 75, 80];
export const BAND_COLORS = ['#86c58f', '#c3e19a', '#f2ec7d', '#f8c35b', '#f28f3b', '#e35a32', '#c1272d', '#8e1b4d', '#4a1a6b'];
export const BASEMAP_FILE = 'basemap/la_county_20261004.pmtiles';
export const CONTEXT_FILES: Record<ContextId, string> = {
  'airport-contours': 'context/la_county_airport_noise_contours.geojson',
  heliports: 'context/la_county_heliports.geojson',
  'county-fire': 'context/la_county_fire_stations.geojson',
  'city-fire': 'context/la_city_fire_stations.geojson',
};
export const PERIOD_KEY: Record<Period, 'd' | 'e' | 'n' | 'q'> = { D: 'd', E: 'e', N: 'n', Q: 'q' };
// field_{d,e,n,q}.pmtiles: Terrarium raster-dem whose elevation is the level in dB. Not modeled is
// encoded as 150 dB (older builds: 0), so smooth (linear) sampling at the coverage edge blends towards
// a louder value, never a quieter one; both sentinels render clear.
const NODATA_DB = 20;
const NOT_MODELED_ABOVE = 100;
const CLEAR = 'rgba(0,0,0,0)';
const TERRAIN_TILES = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
const SATELLITE_TILES = 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}';
const GRAY = '#9ea3a8';

const expr = (value: unknown) => value as ExpressionSpecification;
const bandStep = (input: unknown) => expr(['step', input, BAND_COLORS[0], ...BAND_EDGES.flatMap((edge, i) => [edge, BAND_COLORS[i + 1]])]);
const AIRPORT_CLASS = expr(['to-number', ['get', 'CLASS'], 0]);

function fieldColor(noise: NoiseStyle) {
  if (noise === 'bands') return expr(['step', ['elevation'], CLEAR, NODATA_DB, BAND_COLORS[0], ...BAND_EDGES.flatMap((edge, i) => [edge, BAND_COLORS[i + 1]]), NOT_MODELED_ABOVE, CLEAR]);
  if (noise === 'glow') {
    // Soft heat: quiet places stay clear, louder ones glow brighter and warmer (24 m blurred surface).
    return expr(['interpolate', ['linear'], ['elevation'], 0, CLEAR, 50, 'rgba(242,236,125,0)', 55, 'rgba(248,214,104,0.35)', 60, 'rgba(248,170,80,0.6)', 65, 'rgba(242,120,55,0.78)', 70, 'rgba(227,70,48,0.88)', 75, 'rgba(193,30,60,0.94)', 80, 'rgba(120,20,95,0.97)', 86, 'rgba(60,14,90,1)', NOT_MODELED_ABOVE - 0.1, 'rgba(60,14,90,1)', NOT_MODELED_ABOVE, CLEAR]);
  }
  // Smooth field: each band color sits at its band centre.
  return expr(['interpolate', ['linear'], ['elevation'], 0, CLEAR, NODATA_DB, CLEAR, NODATA_DB + 0.1, BAND_COLORS[0], 42.5, BAND_COLORS[0],
    ...BAND_COLORS.slice(1).flatMap((color, i) => [47.5 + 5 * i, color]), NOT_MODELED_ABOVE - 0.1, BAND_COLORS[BAND_COLORS.length - 1], NOT_MODELED_ABOVE, CLEAR]);
}

export function receiverColor(period: Period) {
  const key = PERIOD_KEY[period];
  return expr(['case', ['any', ['has', 'm'], ['!', ['has', key]]], GRAY, bandStep(['get', key])]);
}

function basemap(theme: BaseTheme, url: string): { sources: StyleSpecification['sources']; layers: LayerSpecification[] } {
  const attribution = '<a href="https://protomaps.com" target="_blank" rel="noreferrer">Protomaps</a> © <a href="https://openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>';
  const sources: StyleSpecification['sources'] = { protomaps: { type: 'vector', url: `pmtiles://${url}${BASEMAP_FILE}`, attribution } };
  if (theme === 'satellite') {
    sources.satellite = { type: 'raster', tiles: [SATELLITE_TILES], tileSize: 256, maxzoom: 16, attribution: 'Imagery: <a href="https://www.usgs.gov/" target="_blank" rel="noreferrer">USGS</a>' };
    return { sources, layers: [{ id: 'satellite', type: 'raster', source: 'satellite', paint: { 'raster-fade-duration': 0 } }, ...basemapLayers('protomaps', namedFlavor('dark'), { lang: 'en', labelsOnly: true })] };
  }
  return { sources, layers: basemapLayers('protomaps', namedFlavor(theme), { lang: 'en' }) };
}

// Terrain is also applied directly by the map component when it changes: MapLibre 6.9's style diff cannot
// change it (it rebuilds the whole style, blanking the map and cutting the 3D camera move). There is no sky:
// with terrain on and the camera tilted, any sky left the map blank after a 2D -> 3D switch.
export function terrainFor(o: StyleOptions): StyleSpecification['terrain'] {
  return o.mode3d ? { source: 'terrain-dem', exaggeration: 1.25 } : undefined;
}


export function buildStyle(o: StyleOptions): StyleSpecification {
  const base = basemap(o.theme, o.layersUrl);
  const dark = o.theme === 'dark' || o.theme === 'satellite';
  const firstLabel = base.layers.findIndex((layer) => layer.type === 'symbol');
  const firstRoad = base.layers.findIndex((layer) => layer.id.startsWith('roads_'));
  const below = base.layers.slice(0, firstRoad < 0 ? firstLabel : firstRoad);
  const middle = base.layers.slice(below.length, firstLabel).filter((layer) => !(o.mode3d && layer.id === 'buildings'));
  const labels = base.layers.slice(firstLabel);
  const showField = o.noise !== 'dots';
  const key = PERIOD_KEY[o.period];
  const visible = (on: boolean) => ({ visibility: on ? 'visible' as const : 'none' as const });
  const sources: StyleSpecification['sources'] = {
    ...base.sources,
    'terrain-dem': { type: 'raster-dem', tiles: [TERRAIN_TILES], encoding: 'terrarium', tileSize: 256, maxzoom: 15, attribution: 'Terrain: Mapzen / AWS Open Data' },
    'hillshade-dem': { type: 'raster-dem', tiles: [TERRAIN_TILES], encoding: 'terrarium', tileSize: 256, maxzoom: 15 },
    field: { type: 'raster-dem', url: `pmtiles://${o.layersUrl}${o.noise === 'glow' ? 'glow' : 'field'}_${PERIOD_KEY[o.period]}.pmtiles`, encoding: 'terrarium', tileSize: 256 },
    receivers: { type: 'vector', url: `pmtiles://${o.layersUrl}receivers.pmtiles` },
    buildings: { type: 'vector', url: `pmtiles://${o.layersUrl}buildings.pmtiles` },
    roads: { type: 'vector', url: `pmtiles://${o.layersUrl}roads.pmtiles` },
    coverage: { type: 'geojson', data: `${o.layersUrl}coverage_outline.geojson` },
  };
  for (const id of Object.keys(CONTEXT_FILES) as ContextId[]) sources[`context-${id}`] = { type: 'geojson', data: `${o.layersUrl}${CONTEXT_FILES[id]}` };
  const noiseLayers: LayerSpecification[] = [
    { id: 'hillshade', type: 'hillshade', source: 'hillshade-dem', paint: { 'hillshade-exaggeration': dark ? 0.25 : 0.18, 'hillshade-shadow-color': dark ? '#000000' : '#5b5f66', 'hillshade-highlight-color': '#ffffff' } },
    { id: 'building-footprints', type: 'fill', source: 'buildings', 'source-layer': 'buildings', minzoom: 14, layout: visible(!o.mode3d), paint: { 'fill-color': dark ? '#d9dde3' : '#ffffff', 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 14, 0.12, 17, 0.32], 'fill-outline-color': dark ? 'rgba(255,255,255,0.35)' : 'rgba(60,64,72,0.35)' } },
  ];
  // Above the basemap streets (so road corridors read as loud), below labels. Linear sampling keeps the
  // surface smooth past its last zoom level (z15, ~5 m pixels) instead of showing blocks.
  const field: LayerSpecification = { id: 'noise-field', type: 'color-relief', source: 'field', layout: visible(showField),
    paint: { 'color-relief-color': fieldColor(o.noise), 'color-relief-opacity': o.noise === 'glow' ? 1 : 0.86, resampling: 'linear' } as never };
  const overlay: LayerSpecification[] = [
    { id: 'roads-modeled-casing', type: 'line', source: 'roads', 'source-layer': 'roads', layout: { ...visible(o.roads), 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': dark ? '#0d1017' : '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1.5, 16, 7] } },
    { id: 'roads-modeled', type: 'line', source: 'roads', 'source-layer': 'roads', layout: { ...visible(o.roads), 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ['step', ['get', 'a'], '#a6b8cc', 2000, '#7f97b5', 10000, '#5b6fa3', 30000, '#463f8e', 100000, '#2a1660'], 'line-width': ['interpolate', ['linear'], ['zoom'], 11, ['step', ['get', 'a'], 0.4, 10000, 0.9, 100000, 1.6], 16, ['step', ['get', 'a'], 2, 10000, 3.5, 100000, 5]], 'line-dasharray': ['case', ['==', ['get', 't'], 'default'], ['literal', [2, 1.2]], ['literal', [1, 0]]] } },
    { id: 'buildings-3d', type: 'fill-extrusion', source: 'buildings', 'source-layer': 'buildings', minzoom: 13, layout: visible(o.mode3d && !o.photo3d),
      paint: { 'fill-extrusion-color': ['case', ['has', key], bandStep(['get', key]), dark ? '#5a606b' : '#c9c4b8'], 'fill-extrusion-height': ['get', 'h'], 'fill-extrusion-base': 0, 'fill-extrusion-opacity': 0.94, 'fill-extrusion-vertical-gradient': true } },
    { id: 'receivers-dots', type: 'circle', source: 'receivers', 'source-layer': 'receivers', minzoom: 12, layout: visible(o.noise === 'dots'),
      paint: { 'circle-color': receiverColor(o.period), 'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 1.2, 15, ['case', ['==', ['get', 'f'], 1], 2.8, 2.2], 18, ['case', ['==', ['get', 'f'], 1], 6, 4.5]], 'circle-opacity': 0.9,
        'circle-stroke-color': dark ? 'rgba(0,0,0,0.5)' : 'rgba(255,255,255,0.7)', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 14, 0, 16, 0.6], 'circle-pitch-alignment': 'map' } },
    // Invisible but rendered, so a click in Field/Bands/Glow still finds the nearest modeled point.
    { id: 'receivers-hit', type: 'circle', source: 'receivers', 'source-layer': 'receivers', minzoom: 14, layout: visible(o.noise !== 'dots'), paint: { 'circle-radius': 7, 'circle-opacity': 0 } },
    { id: 'coverage-outline', type: 'line', source: 'coverage', paint: { 'line-color': dark ? '#e6e9ee' : '#3b4656', 'line-width': 1.4, 'line-opacity': 0.75, 'line-dasharray': [2, 2] } },
    // Official airport CNEL contours: each polygon is the band from CLASS to CLASS + 5 dB, drawn in the
    // same 5 dB colours as the road bands (a different metric; the panel note says so). Louder bands on top.
    { id: 'context-airport-contours', type: 'fill', source: 'context-airport-contours', layout: { ...visible(o.context['airport-contours']), 'fill-sort-key': AIRPORT_CLASS },
      paint: { 'fill-color': bandStep(['+', AIRPORT_CLASS, 0.1]), 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 9, 0.22, 15, 0.1] } },
    { id: 'context-airport-contours-line', type: 'line', source: 'context-airport-contours', layout: { ...visible(o.context['airport-contours']), 'line-sort-key': AIRPORT_CLASS },
      paint: { 'line-color': bandStep(['+', AIRPORT_CLASS, 0.1]), 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.8, 15, 1.8] } },
    { id: 'context-airport-contours-label', type: 'symbol', source: 'context-airport-contours', minzoom: 11,
      layout: { ...visible(o.context['airport-contours']), 'symbol-placement': 'line', 'symbol-spacing': 420, 'text-field': ['concat', ['to-string', AIRPORT_CLASS], ' CNEL'],
        'text-font': ['Noto Sans Medium'], 'text-size': 11, 'text-keep-upright': true },
      paint: { 'text-color': dark ? '#f4f1ea' : '#2b2340', 'text-halo-color': dark ? 'rgba(0,0,0,0.75)' : 'rgba(255,255,255,0.9)', 'text-halo-width': 1.4 } },
    ...(['heliports', 'county-fire', 'city-fire'] as const).map((id): LayerSpecification => ({
      id: `context-${id}`, type: 'circle', source: `context-${id}`, layout: visible(o.context[id]),
      paint: { 'circle-color': id === 'heliports' ? '#2b6cb0' : '#c53030', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 16, 7], 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5 },
    })),
    { id: 'selected-point', type: 'circle', source: 'receivers', 'source-layer': 'receivers', filter: ['==', ['to-string', ['get', 'k']], ''], paint: { 'circle-radius': 9, 'circle-color': CLEAR, 'circle-stroke-color': dark ? '#ffffff' : '#171b22', 'circle-stroke-width': 2.4 } },
    { id: 'selected-building', type: 'line', source: 'buildings', 'source-layer': 'buildings', filter: ['==', ['to-string', ['get', 'k']], ''], paint: { 'line-color': dark ? '#ffffff' : '#171b22', 'line-width': 3 } },
  ];
  return {
    version: 8,
    glyphs: 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf',
    sprite: `https://protomaps.github.io/basemaps-assets/sprites/v4/${dark ? 'dark' : o.theme === 'grayscale' ? 'grayscale' : 'light'}`,
    sources,
    layers: [...below, ...noiseLayers.slice(0, 1), ...middle, field, ...noiseLayers.slice(1), ...overlay, ...labels],
    terrain: terrainFor(o),
    light: { anchor: 'viewport', color: '#ffffff', intensity: 0.35, position: [1.5, 200, 35] },
  };
}
