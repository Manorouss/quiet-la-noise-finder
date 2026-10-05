'use client';

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type FocusEvent, type MutableRefObject, type ReactNode } from 'react';
import { BAND_COLORS, BAND_EDGES, CONTEXT_FILES, ROAD_CLASSES, ROAD_INK, isHospitalPad, type ContextId, type Period } from '@/lib/county-map-style';
import { FIRE_RED, HELIPAD_BLUE, HOSPITAL_RED, stationPath } from '@/lib/map-icons';

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
  return <svg width={size} height={size} viewBox="0 0 22 22" aria-hidden="true"><rect x="1.5" y="1.5" width="19" height="19" rx="5" fill={FIRE_RED} stroke="#fff" strokeWidth="1.2" /><path d={stationPath(11, 11.2, 13)} fill="#fff" fillRule="evenodd" /></svg>;
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

/* Hover cards: the panel shows one line per layer (icon, name, switch); the details and legend open in a card
   beside the panel on hover or keyboard focus, and for a few seconds after a tap on touch screens (over the map,
   below the header). The card is always in the DOM, hidden, so the control is described by it. */
type HintPos = { top: number; left: number; right?: number };
const touchOnly = () => typeof window !== 'undefined' && window.matchMedia('(hover: none)').matches;

function useHint() {
  const ref = useRef<HTMLElement | null>(null);
  const card = useRef<HTMLDivElement | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const [pos, setPos] = useState<HintPos | null>(null);
  const place = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    if (window.innerWidth <= 720) { setPos({ top: 66, left: 10, right: 10 }); return; }
    const guide = el.closest('.map-guide')?.getBoundingClientRect();
    setPos({ top: el.getBoundingClientRect().top - 2, left: (guide?.right ?? el.getBoundingClientRect().right) + 10 });
  }, []);
  const show = useCallback((delay = 280) => { window.clearTimeout(timer.current); timer.current = window.setTimeout(place, delay); }, [place]);
  const hide = useCallback(() => { window.clearTimeout(timer.current); setPos(null); }, []);
  const flash = useCallback(() => { place(); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setPos(null), 4500); }, [place]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  // Keep the card on screen: move it up when it would run past the bottom.
  useLayoutEffect(() => {
    if (!pos || !card.current || pos.right !== undefined) return;
    const over = pos.top + card.current.offsetHeight - (window.innerHeight - 12);
    if (over > 0.5) setPos({ ...pos, top: Math.max(64, pos.top - over) });
  }, [pos]);
  const handlers = {
    onMouseEnter: () => { if (!touchOnly()) show(); },
    onMouseLeave: hide,
    onFocus: (e: FocusEvent<HTMLElement>) => { if (e.target.matches(':focus-visible')) show(0); },
    onBlur: hide,
  };
  return { ref, card, pos, flash, hide, handlers };
}

function HintCard({ id, title, pos, cardRef, children }: { id: string; title: string; pos: HintPos | null; cardRef: MutableRefObject<HTMLDivElement | null>; children: ReactNode }) {
  return <div ref={(el) => { cardRef.current = el; }} id={id} role="tooltip" className={`ml-card ${pos ? 'is-open' : ''}`} style={pos ? { top: pos.top, left: pos.left, right: pos.right } : undefined}>
    <strong>{title}</strong>{children}
  </div>;
}

/** One layer: icon, name, and a switch (or a status such as "Included"); details in the hover card.
 * The whole row is the click target, and nothing opens under it, so it stays put when toggled. */
function Row({ className = '', icon, title, on, onToggle, status, anchor, children }: {
  className?: string; icon: ReactNode; title: string; on?: boolean; onToggle?: (on: boolean) => void; status?: string;
  anchor: (el: HTMLElement | null) => void; children: ReactNode;
}) {
  const id = useId();
  const hint = useHint();
  const body = <>
    <span className="ml-icon">{icon}</span>
    <span className="ml-text">{title}</span>
    {onToggle
      ? <input type="checkbox" role="switch" className="ml-switch" checked={Boolean(on)} aria-describedby={id}
          onChange={(e) => { anchor(hint.ref.current); onToggle(e.target.checked); if (e.target.checked && touchOnly()) hint.flash(); }} />
      : <span className={`ml-status ${status === 'Included' ? 'is-in' : ''}`}>{status}</span>}
  </>;
  return <div ref={(el) => { hint.ref.current = el; }} className={`ml-row ${className} ${on ? 'is-on' : ''}`} {...hint.handlers}>
    {onToggle
      ? <label className="ml-main">{body}</label>
      : <button type="button" className="ml-main" aria-describedby={id} onClick={() => (hint.pos ? hint.hide() : hint.flash())}>{body}</button>}
    <HintCard id={id} title={title} pos={hint.pos} cardRef={hint.card}>{children}</HintCard>
  </div>;
}

/** A small (i) that shows a hint card: for the help text of other sidebar sections. */
export function InfoHint({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  const hint = useHint();
  return <span ref={(el) => { hint.ref.current = el; }} className="ml-info" {...hint.handlers}>
    <button type="button" aria-label={`About ${title}`} aria-describedby={id} onClick={() => (hint.pos ? hint.hide() : hint.flash())}>i</button>
    <HintCard id={id} title={title} pos={hint.pos} cardRef={hint.card}>{children}</HintCard>
  </span>;
}

function TrafficGlyph() {
  return <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
    {[bandColor(52), bandColor(62), bandColor(72)].map((color, i) => <rect key={color} x={2 + i * 8.6} y="4" width="7.4" height="20" rx="2.2" fill={color} />)}
    <path d="M3 21h22" stroke="#1d2733" strokeOpacity=".55" strokeWidth="1.6" strokeLinecap="round" />
  </svg>;
}
function TrainGlyph() {
  return <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><g fill="none" stroke="#4d5a66" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <rect x="7.5" y="4" width="13" height="15" rx="3.5" /><path d="M7.5 12h13M11 23l-2.5 2.5M17 23l2.5 2.5" /><circle cx="11" cy="16" r=".9" fill="#4d5a66" /><circle cx="17" cy="16" r=".9" fill="#4d5a66" /><path d="M10 19.5l-1 3.5h10l-1-3.5" />
  </g></svg>;
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
  // Scroll anchoring: a toggle can change content above the panel (the aircraft switch changes the period and the
  // values in the place panel), so after the update the clicked row is scrolled back to where it was.
  const anchored = useRef<{ el: HTMLElement; top: number } | null>(null);
  const anchor = useCallback((el: HTMLElement | null) => { anchored.current = el ? { el, top: el.getBoundingClientRect().top } : null; }, []);
  useLayoutEffect(() => {
    const a = anchored.current;
    anchored.current = null;
    if (!a) return;
    const delta = a.el.getBoundingClientRect().top - a.top;
    const scroller = a.el.closest('.map-guide');
    if (scroller && Math.abs(delta) > 0.5) scroller.scrollTop += delta;
  });
  return <section className="guide-section ml-panel">
    <h2>In the noise colors</h2>
    <Row icon={<TrafficGlyph />} title="Road traffic" status="Included" anchor={anchor}>
      <p>Always in the colors: every modeled road, day, evening, night and 24 h.</p>
    </Row>
    <Row className="context-airport-contours" icon={<AircraftGlyph />} title="Aircraft" on={aircraft} anchor={anchor}
      onToggle={(on) => { setContext({ ...context, 'airport-contours': on }); if (on) setPeriod('Q'); }}>
      <div className="legend-chips">{[55, 60, 65, 70, 75].map((level) => <span key={level} style={{ background: bandColor(level + 0.1), color: level >= 65 ? '#fff' : '#2b2340' }}>{level}</span>)}<em>dB CNEL</em></div>
      <div className="legend-key"><span><i className="key-line" />Official</span><span><i className="key-line is-dashed" />Estimated</span></div>
      <p>{period === 'Q' ? 'Added to the 24 h colors. Off: road traffic alone.' : 'Counts in the 24 h view only; day, evening and night are roads.'}</p>
    </Row>
    <Row icon={<TrainGlyph />} title="Trains" status="Included" anchor={anchor}>
      <p>Metrolink, Amtrak and freight on the Valley main lines (Ventura and Antelope Valley), in every view. Horns at crossings and other lines are not in yet.</p>
    </Row>
    <Row icon={<HelicopterGlyph />} title="Helicopters" status="Not yet" anchor={anchor}>
      <p>Not modeled yet: LA has no official helicopter contours or routes. It needs flight tracks (ADS-B).</p>
    </Row>
    <Row icon={<SirenGlyph />} title="Sirens" status="Not yet" anchor={anchor}>
      <p>Not modeled yet: it needs how often each station&rsquo;s engines go out, and their routes.</p>
    </Row>
    <h2 className="ml-subhead">On the map only</h2>
    <Row className="ml-roads" icon={<RoadsGlyph />} title="Road lines" on={roads} onToggle={setRoads} anchor={anchor}>
      <div className="legend-roads">{ROAD_CLASSES.map(([min, label, width]) => <span key={min}><i style={{ height: `${width}px` }} />{label}</span>)}</div>
      <p><i className="key-dash" />No count, typical traffic. Vehicles a day; counts show when zoomed in.</p>
    </Row>
    <Row className="context-heliports" icon={<HeliGlyph color={HELIPAD_BLUE} size={24} />} title="Heliports" on={context.heliports} anchor={anchor}
      onToggle={(on) => setContext({ ...context, heliports: on })}>
      <div className="legend-key"><span><HeliGlyph color={HELIPAD_BLUE} size={16} />Helipad</span><span><HeliGlyph color={HOSPITAL_RED} size={16} />Hospital</span></div>
      {places && <p>{places.heliports.length} pads, {hospitals} at hospitals.</p>}
    </Row>
    <Row className="context-fire" icon={<FireGlyph size={24} />} title="Fire stations" on={fireOn} anchor={anchor}
      onToggle={(on) => setContext({ ...context, 'county-fire': on, 'city-fire': on })}>
      <p>{places ? `${places.counts.county} LA County Fire and ${places.counts.city} LA City Fire stations.` : 'LA County Fire and LA City Fire stations.'}</p>
    </Row>
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
