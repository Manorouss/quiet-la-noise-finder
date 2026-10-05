import { layers as basemapLayers, namedFlavor } from '@protomaps/basemaps';
import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from 'maplibre-gl';

export type Period = 'D' | 'E' | 'N' | 'Q';  // Q: 24 h CNEL, roads + aircraft (roads only with aircraft switched off)
export type NoiseStyle = 'field' | 'bands' | 'glow' | 'dots';
export type BaseTheme = 'light' | 'dark' | 'grayscale' | 'satellite';
export type ContextId = 'airport-contours' | 'heliports' | 'county-fire' | 'city-fire';
// context['airport-contours'] is the aircraft switch: it draws the contours and adds aircraft to the 24 h colors.
// roadField: the build has field_r/glow_r (24 h roads only); older builds keep aircraft in the 24 h surface.
export type StyleOptions = { layersUrl: string; period: Period; noise: NoiseStyle; mode3d: boolean; roads: boolean; context: Record<ContextId, boolean>; theme: BaseTheme; photo3d?: boolean; roadField?: boolean };

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
export const AIRPORT_ESTIMATED_FILE = 'context/airport_noise_estimated.geojson';
// Modeled roads: line width (px at z16) by traffic, vehicles a day. One neutral ink so the lines sit on top of
// the noise colors instead of competing with them.
export const ROAD_CLASSES: [number, string, number][] = [[0, '<2k', 0.8], [2000, '2–10k', 1.4], [10000, '10–30k', 2.2], [30000, '30–100k', 3.2], [100000, '100k+', 4.6]];
export const ROAD_INK = { light: '#1d2733', dark: '#f3f5f8' };
export const PERIOD_KEY: Record<Period, 'd' | 'e' | 'n' | 'q'> = { D: 'd', E: 'e', N: 'n', Q: 'q' };
/** 24 h CNEL of road traffic from the day/evening/night levels (12/3/9 hours, +5 and +10 dB), as the pipeline computes it. */
export function roadCnel(d: number | null, e: number | null, n: number | null) {
  if (d === null || e === null || n === null) return null;
  return Math.round(100 * Math.log10((12 * 10 ** (d / 10) + 3 * 10 ** ((e + 5) / 10) + 9 * 10 ** ((n + 10) / 10)) / 24)) / 10;
}
/** The same formula as a style expression on features with d/e/n (prefix '' for the loudest wall, 'l' for the least exposed). */
const roadCnelExpr = (suffix = '') => ['*', 10, ['log10', ['/', ['+',
  ['*', 12, ['^', 10, ['/', ['get', `d${suffix}`], 10]]],
  ['*', 3, ['^', 10, ['/', ['+', ['get', `e${suffix}`], 5], 10]]],
  ['*', 9, ['^', 10, ['/', ['+', ['get', `n${suffix}`], 10], 10]]]], 24]]];
const hasDen = ['all', ['has', 'd'], ['has', 'e'], ['has', 'n']] as unknown as ExpressionSpecification;
/** Whether the 24 h colors are roads only: aircraft switched off. */
export const roadsOnly24h = (o: Pick<StyleOptions, 'period' | 'context'>) => o.period === 'Q' && !o.context['airport-contours'];
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
const HELI_NAME = ['downcase', ['coalesce', ['get', 'name'], '']];
const IS_HOSPITAL_PAD = expr(['any', ['in', 'hospital', HELI_NAME], ['in', 'medic', HELI_NAME]]);
/** Same test as IS_HOSPITAL_PAD, for code outside the style (panel text, nearby search). */
export const isHospitalPad = (name: unknown) => /hospital|medic/i.test(String(name ?? ''));

function fieldColor(noise: NoiseStyle) {
  // Bands: 5 dB steps written as an interpolate ramp with stops 0.01 dB apart (MapLibre 6.9's color-relief
  // drew nothing for a 'step' expression). With linear sampling the band edges are smooth curves.
  if (noise === 'bands') {
    return expr(['interpolate', ['linear'], ['elevation'], 0, CLEAR, NODATA_DB - 0.01, CLEAR, NODATA_DB, BAND_COLORS[0],
      ...BAND_EDGES.flatMap((edge, i) => [edge - 0.01, BAND_COLORS[i], edge, BAND_COLORS[i + 1]]),
      NOT_MODELED_ABOVE - 0.01, BAND_COLORS[BAND_COLORS.length - 1], NOT_MODELED_ABOVE, CLEAR]);
  }
  if (noise === 'glow') {
    // Soft heat: quiet places stay clear, louder ones glow brighter and warmer (24 m blurred surface).
    return expr(['interpolate', ['linear'], ['elevation'], 0, CLEAR, 50, 'rgba(242,236,125,0)', 55, 'rgba(248,214,104,0.35)', 60, 'rgba(248,170,80,0.6)', 65, 'rgba(242,120,55,0.78)', 70, 'rgba(227,70,48,0.88)', 75, 'rgba(193,30,60,0.94)', 80, 'rgba(120,20,95,0.97)', 86, 'rgba(60,14,90,1)', NOT_MODELED_ABOVE - 0.1, 'rgba(60,14,90,1)', NOT_MODELED_ABOVE, CLEAR]);
  }
  // Smooth field: each band color sits at its band centre.
  return expr(['interpolate', ['linear'], ['elevation'], 0, CLEAR, NODATA_DB, CLEAR, NODATA_DB + 0.1, BAND_COLORS[0], 42.5, BAND_COLORS[0],
    ...BAND_COLORS.slice(1).flatMap((color, i) => [47.5 + 5 * i, color]), NOT_MODELED_ABOVE - 0.1, BAND_COLORS[BAND_COLORS.length - 1], NOT_MODELED_ABOVE, CLEAR]);
}

export function receiverColor(period: Period, roadsOnly = false) {
  if (roadsOnly) return expr(['case', ['any', ['has', 'm'], ['!', hasDen]], GRAY, bandStep(roadCnelExpr())]);
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
  const roadsOnly = roadsOnly24h(o);
  const fieldBand = roadsOnly && o.roadField ? 'r' : key;
  const ink = dark ? ROAD_INK.dark : ROAD_INK.light;
  const roadWidth = (scale: number) => expr(['step', ['get', 'a'], ...ROAD_CLASSES.flatMap(([min, , width], i) => (i === 0 ? [width * scale] : [min, width * scale]))]);
  const visible = (on: boolean) => ({ visibility: on ? 'visible' as const : 'none' as const });
  const sources: StyleSpecification['sources'] = {
    ...base.sources,
    'terrain-dem': { type: 'raster-dem', tiles: [TERRAIN_TILES], encoding: 'terrarium', tileSize: 256, maxzoom: 15, attribution: 'Terrain: Mapzen / AWS Open Data' },
    'hillshade-dem': { type: 'raster-dem', tiles: [TERRAIN_TILES], encoding: 'terrarium', tileSize: 256, maxzoom: 15 },
    field: { type: 'raster-dem', url: `pmtiles://${o.layersUrl}${o.noise === 'glow' ? 'glow' : 'field'}_${fieldBand}.pmtiles`, encoding: 'terrarium', tileSize: 256 },
    receivers: { type: 'vector', url: `pmtiles://${o.layersUrl}receivers.pmtiles` },
    buildings: { type: 'vector', url: `pmtiles://${o.layersUrl}buildings.pmtiles` },
    roads: { type: 'vector', url: `pmtiles://${o.layersUrl}roads.pmtiles` },
    coverage: { type: 'geojson', data: `${o.layersUrl}coverage_outline.geojson` },
  };
  for (const id of Object.keys(CONTEXT_FILES) as ContextId[]) sources[`context-${id}`] = { type: 'geojson', data: `${o.layersUrl}${CONTEXT_FILES[id]}` };
  sources['context-airport-estimated'] = { type: 'geojson', data: `${o.layersUrl}${AIRPORT_ESTIMATED_FILE}` };
  const noiseLayers: LayerSpecification[] = [
    { id: 'hillshade', type: 'hillshade', source: 'hillshade-dem', paint: { 'hillshade-exaggeration': dark ? 0.25 : 0.18, 'hillshade-shadow-color': dark ? '#000000' : '#5b5f66', 'hillshade-highlight-color': '#ffffff' } },
    { id: 'building-footprints', type: 'fill', source: 'buildings', 'source-layer': 'buildings', minzoom: 14, layout: visible(!o.mode3d), paint: { 'fill-color': dark ? '#d9dde3' : '#ffffff', 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 14, 0.12, 17, 0.32], 'fill-outline-color': dark ? 'rgba(255,255,255,0.35)' : 'rgba(60,64,72,0.35)' } },
  ];
  // Above the basemap streets (so road corridors read as loud), below labels. Linear sampling keeps the
  // surface smooth past its last zoom level (z15, ~5 m pixels) instead of showing blocks.
  const field: LayerSpecification = { id: 'noise-field', type: 'color-relief', source: 'field', layout: visible(showField),
    paint: { 'color-relief-color': fieldColor(o.noise), 'color-relief-opacity': o.noise === 'glow' ? 1 : 0.86, resampling: 'linear' } as never };
  const overlay: LayerSpecification[] = [
    // Modeled roads: thin ink lines over the noise, wider for more traffic, dashed where the traffic is a typical
    // value (no count); fainter when zoomed out. From z15 each road is labelled with its traffic.
    { id: 'roads-modeled', type: 'line', source: 'roads', 'source-layer': 'roads', layout: { ...visible(o.roads), 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ink, 'line-width': ['interpolate', ['linear'], ['zoom'], 11, roadWidth(0.35), 16, roadWidth(1), 19, roadWidth(1.8)],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 11, 0.35, 14, 0.5, 17, 0.62], 'line-blur': 0.4,
        'line-dasharray': ['case', ['==', ['get', 't'], 'default'], ['literal', [2.2, 1.6]], ['literal', [1, 0]]] } },
    { id: 'roads-modeled-label', type: 'symbol', source: 'roads', 'source-layer': 'roads', minzoom: 15, layout: { ...visible(o.roads), 'symbol-placement': 'line', 'symbol-spacing': 360,
        'text-field': ['concat', ['case', ['==', ['get', 't'], 'default'], '~', ''], ['case', ['>=', ['get', 'a'], 1000],
          ['concat', ['number-format', ['/', ['get', 'a'], 1000], { 'max-fraction-digits': ['case', ['<', ['get', 'a'], 10000], 1, 0] }], 'k'], ['to-string', ['get', 'a']]], ' / day'],
        'text-font': ['Noto Sans Medium'], 'text-size': 10, 'text-keep-upright': true, 'text-padding': 8 },
      paint: { 'text-color': ink, 'text-opacity': 0.8, 'text-halo-color': dark ? 'rgba(0,0,0,0.7)' : 'rgba(255,255,255,0.85)', 'text-halo-width': 1.2 } },
    { id: 'buildings-3d', type: 'fill-extrusion', source: 'buildings', 'source-layer': 'buildings', minzoom: 13, layout: visible(o.mode3d && !o.photo3d),
      paint: { 'fill-extrusion-color': roadsOnly ? ['case', hasDen, bandStep(roadCnelExpr()), dark ? '#5a606b' : '#c9c4b8'] : ['case', ['has', key], bandStep(['get', key]), dark ? '#5a606b' : '#c9c4b8'], 'fill-extrusion-height': ['get', 'h'], 'fill-extrusion-base': 0, 'fill-extrusion-opacity': 0.94, 'fill-extrusion-vertical-gradient': true } },
    { id: 'receivers-dots', type: 'circle', source: 'receivers', 'source-layer': 'receivers', minzoom: 12, layout: visible(o.noise === 'dots'),
      paint: { 'circle-color': receiverColor(o.period, roadsOnly), 'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 1.2, 15, ['case', ['==', ['get', 'f'], 1], 2.8, 2.2], 18, ['case', ['==', ['get', 'f'], 1], 6, 4.5]], 'circle-opacity': 0.9,
        'circle-stroke-color': dark ? 'rgba(0,0,0,0.5)' : 'rgba(255,255,255,0.7)', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 14, 0, 16, 0.6], 'circle-pitch-alignment': 'map' } },
    // Invisible but rendered, so a click in Field/Bands/Glow still finds the nearest modeled point.
    { id: 'receivers-hit', type: 'circle', source: 'receivers', 'source-layer': 'receivers', minzoom: 14, layout: visible(o.noise !== 'dots'), paint: { 'circle-radius': 7, 'circle-opacity': 0 } },
    { id: 'coverage-outline', type: 'line', source: 'coverage', paint: { 'line-color': dark ? '#e6e9ee' : '#3b4656', 'line-width': 1.4, 'line-opacity': 0.75, 'line-dasharray': [2, 2] } },
    // Official airport CNEL contours: each polygon is the band from CLASS to CLASS + 5 dB, drawn in the
    // same 5 dB colours as the noise. In the 24 h view the noise colors already include aircraft, so the fill
    // is invisible (still clickable); in day/evening/night views it tints the aircraft areas.
    { id: 'context-airport-contours', type: 'fill', source: 'context-airport-contours', layout: { ...visible(o.context['airport-contours']), 'fill-sort-key': AIRPORT_CLASS },
      paint: { 'fill-color': bandStep(['+', AIRPORT_CLASS, 0.1]), 'fill-opacity': o.period === 'Q' ? 0 : ['interpolate', ['linear'], ['zoom'], 9, 0.22, 15, 0.1] } },
    { id: 'context-airport-contours-line', type: 'line', source: 'context-airport-contours', layout: { ...visible(o.context['airport-contours']), 'line-sort-key': AIRPORT_CLASS },
      paint: { 'line-color': bandStep(['+', AIRPORT_CLASS, 0.1]), 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.8, 15, 1.8] } },
    { id: 'context-airport-contours-label', type: 'symbol', source: 'context-airport-contours', minzoom: 11,
      layout: { ...visible(o.context['airport-contours']), 'symbol-placement': 'line', 'symbol-spacing': 420, 'text-field': ['concat', ['to-string', AIRPORT_CLASS], ' CNEL'],
        'text-font': ['Noto Sans Medium'], 'text-size': 11, 'text-keep-upright': true },
      paint: { 'text-color': dark ? '#f4f1ea' : '#2b2340', 'text-halo-color': dark ? 'rgba(0,0,0,0.75)' : 'rgba(255,255,255,0.9)', 'text-halo-width': 1.4 } },
    // Estimated contours beyond the official lines (the 24 h view uses them): dashed, same band colours.
    { id: 'context-airport-estimated', type: 'line', source: 'context-airport-estimated', layout: visible(o.context['airport-contours']),
      paint: { 'line-color': bandStep(['+', AIRPORT_CLASS, 0.1]), 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.8, 15, 1.6], 'line-dasharray': [3, 2.2], 'line-opacity': 0.9 } },
    { id: 'context-airport-estimated-label', type: 'symbol', source: 'context-airport-estimated', minzoom: 11,
      layout: { ...visible(o.context['airport-contours']), 'symbol-placement': 'line', 'symbol-spacing': 420, 'text-field': ['concat', ['to-string', AIRPORT_CLASS], ' CNEL (est.)'],
        'text-font': ['Noto Sans Medium'], 'text-size': 10.5, 'text-keep-upright': true },
      paint: { 'text-color': dark ? '#d9d4e6' : '#4b4560', 'text-halo-color': dark ? 'rgba(0,0,0,0.75)' : 'rgba(255,255,255,0.9)', 'text-halo-width': 1.4 } },
    // Heliports (an H; red for hospital helipads, where medical helicopters land) and fire stations (sirens),
    // drawn as icons (map-icons.ts) with names from street zoom.
    { id: 'context-heliports', type: 'symbol', source: 'context-heliports', layout: { ...visible(o.context.heliports),
        'icon-image': ['case', IS_HOSPITAL_PAD, 'ql-heliport-hospital', 'ql-heliport'],
        'icon-size': ['interpolate', ['linear'], ['zoom'], 9, 0.55, 15, 1], 'icon-allow-overlap': true,
        'text-field': ['step', ['zoom'], '', 14, ['coalesce', ['get', 'name'], '']], 'text-font': ['Noto Sans Medium'], 'text-size': 11,
        'text-anchor': 'top', 'text-offset': [0, 1.2], 'text-optional': true, 'text-max-width': 9 },
      paint: { 'text-color': dark ? '#dfe7f5' : '#1f3d6b', 'text-halo-color': dark ? 'rgba(0,0,0,0.8)' : 'rgba(255,255,255,0.92)', 'text-halo-width': 1.4 } },
    ...(['county-fire', 'city-fire'] as const).map((id): LayerSpecification => ({
      id: `context-${id}`, type: 'symbol', source: `context-${id}`, layout: { ...visible(o.context[id]),
        'icon-image': 'ql-fire', 'icon-size': ['interpolate', ['linear'], ['zoom'], 9, 0.5, 15, 0.95], 'icon-allow-overlap': true,
        'text-field': ['step', ['zoom'], '', 14, ['concat', 'Station ', ['to-string', ['coalesce', ['get', 'station'], '']]]], 'text-font': ['Noto Sans Medium'], 'text-size': 11,
        'text-anchor': 'top', 'text-offset': [0, 1.2], 'text-optional': true },
      paint: { 'text-color': dark ? '#f6dede' : '#7d1f1f', 'text-halo-color': dark ? 'rgba(0,0,0,0.8)' : 'rgba(255,255,255,0.92)', 'text-halo-width': 1.4 },
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
