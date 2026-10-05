'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { BAND_COLORS, BAND_EDGES, CONTEXT_FILES, ROAD_CLASSES, ROAD_INK, isHospitalPad, type ContextId, type Period } from '@/lib/county-map-style';
import { FIRE_RED, HELIPAD_BLUE, HOSPITAL_RED, flamePath } from '@/lib/map-icons';

/** A heliport or fire station from the context layers, for counts and the "Nearby" line. */
export type Place = { at: [number, number]; name: string; kind: 'heliport' | 'hospital' | 'fire'; layer: ContextId };
export type Places = { heliports: Place[]; fire: Place[]; counts: { county: number; city: number } };

const bandColor = (value: number) => BAND_COLORS[Math.max(0, BAND_EDGES.findIndex((edge) => value < edge))] ?? BAND_COLORS[BAND_COLORS.length - 1];

const tidy = (text: string) => (text === text.toUpperCase() ? text.toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase()) : text).trim();

/** Load heliports and fire stations once, a moment after the map starts (about 250 kB, compressed far less). */
export function usePlaces(layersUrl: string) {
  const [places, setPlaces] = useState<Places | null>(null);
  useEffect(() => {
    let cancelled = false;
    type Doc = { features?: { properties?: Record<string, unknown>; geometry?: { type: string; coordinates: [number, number] } }[] };
    const load = (id: ContextId) => fetch(`${layersUrl}${CONTEXT_FILES[id]}`).then((r) => (r.ok ? r.json() as Promise<Doc> : { features: [] })).catch(() => ({ features: [] }) as Doc);
    const points = (doc: Doc, layer: ContextId, kind: (p: Record<string, unknown>) => Place['kind'], name: (p: Record<string, unknown>) => string) =>
      (doc.features ?? []).filter((f) => f.geometry?.type === 'Point').map((f) => ({ at: f.geometry!.coordinates, layer, kind: kind(f.properties ?? {}), name: name(f.properties ?? {}) }));
    const timer = window.setTimeout(() => {
      Promise.all([load('heliports'), load('county-fire'), load('city-fire')]).then(([heli, county, city]) => {
        if (cancelled) return;
        const station = (p: Record<string, unknown>) => (p.station ? `Fire station ${String(p.station)}` : 'Fire station');
        const countyFire = points(county, 'county-fire', () => 'fire', station);
        const cityFire = points(city, 'city-fire', () => 'fire', station);
        setPlaces({
          heliports: points(heli, 'heliports', (p) => (isHospitalPad(p.name) ? 'hospital' : 'heliport'), (p) => tidy(String(p.name || 'Heliport'))),
          fire: [...countyFire, ...cityFire],
          counts: { county: countyFire.length, city: cityFire.length },
        });
      });
    }, 1200);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [layersUrl]);
  return places;
}

/** Great-circle distance in miles. */
export function miles(a: [number, number], b: [number, number]) {
  const rad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * rad, dLng = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLng / 2) ** 2;
  return 3958.8 * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
}

function nearest(list: Place[], at: [number, number]) {
  let best: { place: Place; mi: number } | null = null;
  for (const place of list) {
    const mi = miles(at, place.at);
    if (!best || mi < best.mi) best = { place, mi };
  }
  return best;
}

/* Panel icons: the same symbols the map draws. */
export function HeliGlyph({ color, size = 18 }: { color: string; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 22 22" aria-hidden="true"><circle cx="11" cy="11" r="9.4" fill="#fff" stroke={color} strokeWidth="2.2" /><path d="M7.4 6.2h2.1v3.9h3V6.2h2.1v9.6h-2.1v-3.9h-3v3.9H7.4z" fill={color} /></svg>;
}
export function FireGlyph({ size = 18 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 22 22" aria-hidden="true"><rect x="1.5" y="1.5" width="19" height="19" rx="5" fill={FIRE_RED} stroke="#fff" strokeWidth="1.2" /><path d={flamePath(11, 11.5, 12.5)} fill="#fff" /></svg>;
}
function RoadsGlyph() {
  return <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
    <rect x="1" y="1" width="26" height="26" rx="6" fill={bandColor(57)} opacity=".45" />
    <path d="M3 22 C10 19 16 12 25 7" stroke={ROAD_INK.light} strokeOpacity=".75" strokeWidth="3.6" fill="none" strokeLinecap="round" />
    <path d="M3 13 C8 13 12 16 16 22" stroke={ROAD_INK.light} strokeOpacity=".65" strokeWidth="2" fill="none" strokeLinecap="round" />
    <path d="M12 3 C13 8 17 11 25 15" stroke={ROAD_INK.light} strokeOpacity=".55" strokeWidth="1.2" fill="none" strokeLinecap="round" strokeDasharray="2.6 1.8" />
  </svg>;
}
function AircraftGlyph() {
  return <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
    <ellipse cx="14" cy="14" rx="12.5" ry="8.5" fill={bandColor(55.1)} opacity=".55" />
    <ellipse cx="14" cy="14" rx="8.8" ry="5.6" fill={bandColor(65.1)} opacity=".8" />
    <ellipse cx="14" cy="14" rx="5" ry="2.9" fill={bandColor(75.1)} />
    <path d="M14 9.2l1 3.6 4.4 1.4v1l-4.4-.6-.4 2.6 1.4 1v.8L14 18.4l-2 .6V18.2l1.4-1-.4-2.6-4.4.6v-1l4.4-1.4z" fill="#fff" />
  </svg>;
}

function LayerRow({ className, icon, title, meta, on, onChange, children }: { className: string; icon: ReactNode; title: string; meta: ReactNode; on: boolean; onChange: (on: boolean) => void; children?: ReactNode }) {
  return <div className={`ml-row ${className} ${on ? 'is-on' : ''}`}>
    <label className="ml-main">
      <span className="ml-icon">{icon}</span>
      <span className="ml-text"><strong>{title}</strong><small>{meta}</small></span>
      <input type="checkbox" role="switch" className="ml-switch" checked={on} onChange={(e) => onChange(e.target.checked)} />
    </label>
    {on && children && <div className="ml-legend">{children}</div>}
  </div>;
}

function SourceRow({ icon, title, meta, status, children }: { icon: ReactNode; title: string; meta: string; status: string; children: ReactNode }) {
  return <details className="ml-row ml-source">
    <summary className="ml-main">
      <span className="ml-icon">{icon}</span>
      <span className="ml-text"><strong>{title}</strong><small>{meta}</small></span>
      <span className={`ml-status ${status === 'Included' ? 'is-in' : ''}`}>{status}</span>
    </summary>
    <div className="ml-legend">{children}</div>
  </details>;
}

function TrafficGlyph() {
  return <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
    {[bandColor(52), bandColor(62), bandColor(72)].map((color, i) => <rect key={color} x={2 + i * 8.6} y="4" width="7.4" height="20" rx="2.2" fill={color} />)}
    <path d="M3 21h22" stroke="#1d2733" strokeOpacity=".55" strokeWidth="1.6" strokeLinecap="round" />
  </svg>;
}
function HelicopterGlyph() {
  return <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><g fill="none" stroke="#7b8590" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 7h18M14 7v3" /><path d="M9.5 15.5c0-3 2.2-5.5 5.4-5.5 3 0 5.1 2.4 5.1 5.4V18h-7.6c-1.6 0-2.9-1.1-2.9-2.5z" /><path d="M9.6 15H4M2.5 12.5l1.5 2.5M11 21h9M13 18v3M18 18v3" />
  </g></svg>;
}
function SirenGlyph() {
  return <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><g fill="none" stroke="#7b8590" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M8.5 20v-6a5.5 5.5 0 0 1 11 0v6" /><path d="M6 23h16M14 4v2.5M5.5 8l1.8 1.6M22.5 8l-1.8 1.6" />
  </g></svg>;
}

export function LayerPanel({ roads, setRoads, context, setContext, period, setPeriod, places }: {
  roads: boolean; setRoads: (on: boolean) => void; context: Record<ContextId, boolean>; setContext: (next: Record<ContextId, boolean>) => void;
  period: Period; setPeriod: (period: Period) => void; places: Places | null;
}) {
  const fireOn = context['county-fire'] || context['city-fire'];
  const aircraft = context['airport-contours'];
  const hospitals = places?.heliports.filter((p) => p.kind === 'hospital').length;
  return <section className="guide-section ml-panel">
    <h2>In the noise colors</h2>
    <SourceRow icon={<TrafficGlyph />} title="Road traffic" meta="Every road, freeways to side streets" status="Included">
      <p className="ml-note">Day, evening, night and 24 h levels from every modeled road. This is the base of the map, so it is always on.</p>
    </SourceRow>
    <LayerRow className="context-airport-contours" icon={<AircraftGlyph />} title="Aircraft" meta={aircraft ? 'Added to the 24 h colors' : 'Off: 24 h shows roads only'} on={aircraft}
      onChange={(on) => { setContext({ ...context, 'airport-contours': on }); if (on) setPeriod('Q'); }}>
      <div className="legend-chips">{[55, 60, 65, 70, 75].map((level) => <span key={level} style={{ background: bandColor(level + 0.1), color: level >= 65 ? '#fff' : '#2b2340' }}>{level}</span>)}<em>dB CNEL</em></div>
      <div className="legend-key"><span><i className="key-line" />Official contour</span><span><i className="key-line is-dashed" />Estimated, past the official lines</span></div>
      {period === 'Q'
        ? <p className="ml-note">The 24 h colors include aircraft. Switch off to see road traffic alone.</p>
        : <p className="ml-note">Aircraft counts only in the 24 h view (CNEL); day, evening and night show roads. <button type="button" className="ml-link" onClick={() => setPeriod('Q')}>Show 24 h</button></p>}
      <details className="ml-more"><summary>How the aircraft estimate works</summary>
        <p>CNEL is a 24-hour average that counts evening noise 5 dB and night noise 10 dB louder. Most official airport maps stop at 65 CNEL, so each airport&rsquo;s contours are extended to 55 CNEL from the spacing of its official lines; levels between lines are interpolated. Contours older than 2000 (Compton, El Monte, Torrance, Palmdale, Agua Dulce, Catalina) are drawn but not counted.</p>
      </details>
    </LayerRow>
    <SourceRow icon={<HelicopterGlyph />} title="Helicopters" meta="Police, news, medical and tour flights" status="Not yet">
      <p className="ml-note">Not in the colors yet. Unlike airports, helicopters have no official noise contours or fixed routes in LA; computing them needs flight tracks (for example from ADS-B receivers). The Heliports layer below shows where they land.</p>
    </SourceRow>
    <SourceRow icon={<SirenGlyph />} title="Sirens" meta="Fire engines and ambulances" status="Not yet">
      <p className="ml-note">Not in the colors yet. Sirens are short, very loud events; adding them needs how often each station&rsquo;s engines go out and which streets they take. The Fire stations layer below shows where they start.</p>
    </SourceRow>
    <h2 className="ml-subhead">On the map only <span>does not change the colors</span></h2>
    <LayerRow className="ml-roads" icon={<RoadsGlyph />} title="Road lines" meta="Line width by traffic, vehicles a day" on={roads} onChange={setRoads}>
      <div className="legend-roads">{ROAD_CLASSES.map(([min, label, width]) => <span key={min}><i style={{ height: `${width}px` }} />{label}</span>)}</div>
      <p className="ml-note"><i className="key-dash" />Dashed: no traffic count on that street, so the model uses a typical value for its type. Counts show along the roads when you zoom in; click a road for details.</p>
    </LayerRow>
    <LayerRow className="context-heliports" icon={<HeliGlyph color={HELIPAD_BLUE} size={24} />} title="Heliports"
      meta={places ? `${places.heliports.length} pads · ${hospitals} at hospitals` : 'Helipads and helistops'} on={context.heliports} onChange={(on) => setContext({ ...context, heliports: on })}>
      <div className="legend-key"><span><HeliGlyph color={HELIPAD_BLUE} size={16} />Heliport or helistop</span><span><HeliGlyph color={HOSPITAL_RED} size={16} />Hospital helipad</span></div>
      <p className="ml-note">Many rooftop pads are for emergencies only. Names appear when you zoom in.</p>
    </LayerRow>
    <LayerRow className="context-fire" icon={<FireGlyph size={24} />} title="Fire stations"
      meta={places ? `${places.fire.length} stations · County and City of LA` : 'County and City of LA'} on={fireOn} onChange={(on) => setContext({ ...context, 'county-fire': on, 'city-fire': on })}>
      <p className="ml-note">{places ? `${places.counts.county} LA County Fire and ${places.counts.city} LA City Fire stations` : 'LA County Fire and LA City Fire stations'} (two departments: the City of LA runs its own). Station numbers appear when you zoom in.</p>
    </LayerRow>
  </section>;
}

/** Nearest fire station and helipads to a selected place, as buttons that show both on the map. */
export function Nearby({ at, places, onShow }: { at: [number, number]; places: Places | null; onShow: (place: Place) => void }) {
  if (!places) return null;
  const fire = nearest(places.fire, at);
  const hospital = nearest(places.heliports.filter((p) => p.kind === 'hospital'), at);
  const pad = nearest(places.heliports.filter((p) => p.kind === 'heliport'), at);
  const items = [
    fire && fire.mi <= 3 ? { ...fire, label: fire.place.name, icon: <FireGlyph size={16} /> } : null,
    hospital && hospital.mi <= 3 ? { ...hospital, label: `Hospital helipad · ${hospital.place.name}`, icon: <HeliGlyph color={HOSPITAL_RED} size={16} /> } : null,
    pad && pad.mi <= 1 ? { ...pad, label: `Helipad · ${pad.place.name}`, icon: <HeliGlyph color={HELIPAD_BLUE} size={16} /> } : null,
  ].filter((item): item is NonNullable<typeof item> => item !== null);
  if (!items.length) return null;
  return <div className="nearby" aria-label="Nearby">
    <span className="nearby-label">Nearby</span>
    {items.map((item) => <button type="button" key={item.label} className="nearby-item" onClick={() => onShow(item.place)} title="Show on the map">
      {item.icon}<span>{item.label}</span><strong>{item.mi < 0.1 ? '<0.1' : item.mi.toFixed(1)} mi</strong></button>)}
    {fire && fire.mi < 0.3 && <p className="receiver-meta">Close to a fire station: expect sirens when engines go out. Sirens are not in the values.</p>}
  </div>;
}
