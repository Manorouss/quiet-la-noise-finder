// Street addresses from LA County's public GIS (no key, CORS-enabled for this site).
// A building's address is the situs address of the Assessor parcel under its centre; when the
// point misses every parcel (street, gap), the nearest point of the County Address Management
// System (CAMS) locator is used instead and marked as approximate.
const GIS = 'https://public.gis.lacounty.gov/public/rest/services';
const PARCELS = `${GIS}/LACounty_Cache/LACounty_Parcel/MapServer/0/query`;
const LOCATOR = `${GIS}/CAMS_Locator/GeocodeServer`;
const SEARCH_RADIUS_M = 60;
const MIN_SCORE = 80;

export type Address = { text: string; near: boolean };
type Situs = { SitusHouseNo?: string; SitusFraction?: string; SitusDirection?: string; SitusStreet?: string; SitusUnit?: string; SitusCity?: string; SitusZIP?: string };
type ParcelResponse = { features?: { attributes: Situs }[]; error?: unknown };
type ReverseResponse = { address?: { Street?: string; City?: string; State?: string; ZIP?: string }; error?: unknown };
type CandidatesResponse = { candidates?: { address: string; location: { x: number; y: number }; score: number }[]; error?: unknown };
const cache = new Map<string, Address | null>();

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`address service ${response.status}`);
  return response.json() as Promise<T>;
}

/** "19305 REDWING ST" -> "19305 Redwing St"; keeps N/S/E/W and ordinals like 1st readable. */
function tidy(text: string): string {
  return text.trim().split(/\s+/).map((word) => {
    if (/^(N|S|E|W|NE|NW|SE|SW)$/.test(word)) return word;
    if (/^\d+(ST|ND|RD|TH)$/.test(word)) return word.toLowerCase();
    return word.charAt(0) + word.slice(1).toLowerCase();
  }).join(' ');
}

async function parcelAddress(lng: number, lat: number, signal?: AbortSignal): Promise<string | null> {
  const query = new URLSearchParams({
    geometry: JSON.stringify({ x: lng, y: lat, spatialReference: { wkid: 4326 } }), geometryType: 'esriGeometryPoint', inSR: '4326',
    spatialRel: 'esriSpatialRelIntersects', outFields: 'SitusHouseNo,SitusFraction,SitusDirection,SitusStreet,SitusUnit,SitusCity,SitusZIP',
    returnGeometry: 'false', f: 'json',
  });
  const parcels = (await getJson<ParcelResponse>(`${PARCELS}?${query}`, signal)).features?.map((f) => f.attributes).filter((a) => a.SitusHouseNo && a.SitusStreet) ?? [];
  const a = parcels[0];
  if (!a) return null;
  // Stacked condo parcels share a point: show the building's street address, not one unit.
  const unit = parcels.length === 1 && a.SitusUnit ? ` ${a.SitusUnit.trim()}` : '';
  const street = tidy(`${a.SitusHouseNo}${a.SitusFraction ? ` ${a.SitusFraction}` : ''} ${a.SitusDirection ?? ''} ${a.SitusStreet}${unit}`);
  const city = a.SitusCity ? tidy(a.SitusCity.replace(/\s+CA$/i, '')) : '';
  return `${street}${city ? `, ${city}` : ''}, CA${a.SitusZIP ? ` ${a.SitusZIP.slice(0, 5)}` : ''}`;
}

async function nearestAddress(lng: number, lat: number, signal?: AbortSignal): Promise<string | null> {
  const location = JSON.stringify({ x: lng, y: lat, spatialReference: { wkid: 4326 } });
  const a = (await getJson<ReverseResponse>(`${LOCATOR}/reverseGeocode?${new URLSearchParams({ location, distance: String(SEARCH_RADIUS_M), outSR: '4326', f: 'json' })}`, signal)).address;
  return a?.Street ? `${a.Street}${a.City ? `, ${a.City}` : ''}, ${a.State || 'CA'}${a.ZIP ? ` ${a.ZIP}` : ''}` : null;
}

/** Address of the parcel at this point, else the nearest address on record within 60 m (near: true); null if none. */
export async function addressAt(lng: number, lat: number, signal?: AbortSignal): Promise<Address | null> {
  const key = `${lng.toFixed(6)},${lat.toFixed(6)}`;
  if (cache.has(key)) return cache.get(key) ?? null;
  const parcel = await parcelAddress(lng, lat, signal);
  const near = parcel ? null : await nearestAddress(lng, lat, signal);
  const result = parcel ? { text: parcel, near: false } : near ? { text: near, near: true } : null;
  cache.set(key, result);
  return result;
}

/** Best match for a typed LA County address, or null when the locator is not confident. */
export async function findAddress(text: string, signal?: AbortSignal): Promise<{ lng: number; lat: number; label: string } | null> {
  const data = await getJson<CandidatesResponse>(`${LOCATOR}/findAddressCandidates?${new URLSearchParams({ 'Single Line Input': text, maxLocations: '1', outSR: '4326', f: 'json' })}`, signal);
  const best = data.candidates?.[0];
  return best && best.score >= MIN_SCORE ? { lng: best.location.x, lat: best.location.y, label: best.address } : null;
}
