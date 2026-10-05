'use client';

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import CountyMap, { type Camera, type MapStatus, type Selection, type Values } from '@/components/CountyMap';
import { BAND_COLORS, BAND_EDGES, type BaseTheme, type ContextId, type NoiseStyle, type Period } from '@/lib/county-map-style';
import { addressAt, findAddress, suggestAddresses, type Address, type Suggestion } from '@/lib/address';

const LAYERS_URL = process.env.NEXT_PUBLIC_QUIET_LA_LAYERS_URL || '/county-layers/';
// Photo 3D showcase (lidar + aerial imagery as Gaussian splats): Studio City, Ventura Blvd and US-101.
const SPLAT_SCENE = `${LAYERS_URL}splats/showcase_101_ventura`;
const SPLAT_VIEW = { lng: -118.3721, lat: 34.1474, zoom: 17.3, pitch: 62, bearing: -35 };
const PERIOD_NAME: Record<Period, string> = { D: 'Day', E: 'Evening', N: 'Night', Q: '24 h' };
const UNIT = (period: Period) => (period === 'Q' ? 'dB CNEL · 24 h, roads + aircraft' : `dB LAeq · ${PERIOD_NAME[period].toLowerCase()}`);
const CONTEXT_DEFAULTS: Record<ContextId, boolean> = { 'airport-contours': false, heliports: false, 'county-fire': false, 'city-fire': false };
const CONTEXT_LABELS: Record<ContextId, string> = { 'airport-contours': 'Aircraft noise (official contours, CNEL)', heliports: 'Heliports', 'county-fire': 'LA County fire stations', 'city-fire': 'City of LA fire stations' };
const STYLE_HELP: Record<NoiseStyle, string> = {
  field: 'A smooth surface interpolated from the modeled points, 5 m detail.',
  bands: 'The same surface in 5 dB steps, like an official noise map.',
  glow: 'Only the louder places glow; quieter areas stay clear.',
  dots: 'Every modeled point: building walls (larger) and open ground.',
};
const PLACES: Record<string, [number, number]> = { tarzana: [-118.553, 34.172], reseda: [-118.536, 34.201], 'van nuys': [-118.449, 34.186], northridge: [-118.536, 34.228], encino: [-118.501, 34.159], 'lake balboa': [-118.497, 34.197] };
type Layers = { tiles: string[]; receiver_count: number; building_count: number; built_at_utc: string; building_percentiles?: Partial<Record<'d' | 'e' | 'n' | 'q', number[]>> };

function band(value: number | null) {
  if (value === null) return null;
  const index = BAND_EDGES.findIndex((edge) => value < edge);
  return BAND_COLORS[index < 0 ? BAND_COLORS.length - 1 : index];
}

function Choice<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: readonly (readonly [T, string])[]; onChange: (value: T) => void }) {
  function keys(event: KeyboardEvent<HTMLDivElement>) {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const index = options.findIndex(([id]) => id === value);
    const next = (index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + options.length) % options.length;
    onChange(options[next][0]);
    event.currentTarget.querySelectorAll<HTMLButtonElement>('button')[next]?.focus();
  }
  return <div className="choice" role="radiogroup" aria-label={label} onKeyDown={keys}>{options.map(([id, text]) => <button type="button" key={id} role="radio" aria-checked={value === id} tabIndex={value === id ? 0 : -1} onClick={() => onChange(id)}>{text}</button>)}</div>;
}

function ValueRows({ values, period }: { values: Values; period: Period }) {
  return <div className="county-values">{(['D', 'E', 'N', 'Q'] as Period[]).map((p) => <div key={p} className={p === period ? 'is-active' : ''}><span>{p === 'Q' ? '24 h CNEL' : PERIOD_NAME[p]}</span><i style={{ background: band(values[p]) ?? '#9ea3a8' }} /><strong>{values[p] === null ? '—' : `${values[p]!.toFixed(1)} dB`}</strong></div>)}</div>;
}

function AddressLine({ at, near, known }: { at: [number, number]; near?: boolean; known?: string }) {
  const key = `${at[0]},${at[1]}`;
  const [result, setResult] = useState<{ key: string; address: Address | null; failed: boolean } | null>(null);
  useEffect(() => {
    if (known) return;
    const controller = new AbortController();
    addressAt(at[0], at[1], controller.signal)
      .then((address) => setResult({ key, address, failed: false }))
      .catch(() => { if (!controller.signal.aborted) setResult({ key, address: null, failed: true }); });
    return () => controller.abort();
  }, [key, at, known]);
  if (known) return <p className="selection-address">{known}</p>;
  const current = result?.key === key ? result : null;
  if (!current) return <p className="selection-address is-pending">Finding the address…</p>;
  if (!current.address) return <p className="selection-address is-pending">{current.failed ? 'Address lookup is unavailable right now.' : 'No street address on record here.'}</p>;
  return <p className="selection-address">{near || current.address.near ? <span>Near </span> : null}{current.address.text}</p>;
}

// Plain-language reading of a level (outdoor, at the building or on open ground).
const LEVEL_WORDS: [number, string, string][] = [
  [45, 'Quiet', 'like a residential street away from through traffic'],
  [55, 'Moderate', 'typical of a residential street'],
  [65, 'Noticeable', 'typical near a busy street'],
  [70, 'Loud', 'typical beside an arterial road or near a freeway'],
  [Infinity, 'Very loud', 'typical beside a major arterial or a freeway'],
];

function LevelWords({ value, period }: { value: number | null; period: Period }) {
  if (value === null) return null;
  const [, label, words] = LEVEL_WORDS.find(([edge]) => value < edge)!;
  const guideline = period === 'Q' ? 53 : period === 'N' ? 45 : null;
  const who = guideline === null ? '' : ` ${value > guideline ? 'Above' : 'Within'} the WHO guideline for road traffic (${guideline} dB ${period === 'Q' ? 'over 24 h' : 'at night'}).`;
  return <p className="level-words"><strong>{label}</strong> · {words}.{who}</p>;
}

// Where a building's loudest wall falls among all mapped buildings (percentiles from layers.json).
function Compare({ value, period, percentiles }: { value: number | null; period: Period; percentiles?: Layers['building_percentiles'] }) {
  const table = percentiles?.[({ D: 'd', E: 'e', N: 'n', Q: 'q' } as const)[period]];
  if (value === null || !table || table.length !== 101) return null;
  let below = table.findIndex((p) => p >= value);
  below = below < 0 ? 100 : below;
  const text = below >= 50 ? `Louder than ${below}% of mapped buildings` : `Quieter than ${100 - below}% of mapped buildings`;
  return <p className="receiver-meta compare-line">{text} ({period === 'Q' ? '24 h' : PERIOD_NAME[period].toLowerCase()}, loudest wall).</p>;
}

function Aircraft({ value }: { value: number | null }) {
  if (value === null) return null;
  return <p className="receiver-meta">Aircraft here: about {value.toFixed(0)} dB CNEL, estimated from the official airport contours; included in the 24 h value.</p>;
}

type Ring = [number, number][];
function insideRing(lng: number, lat: number, ring: Ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > lat) !== (yj > lat) && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// Saved places to compare (kept in this browser only).
type Saved = { id: string; at: [number, number]; label: string; values: Values };
const SAVED_KEY = 'quiet-la-map-saved-v1';
const SAVED_MAX = 5;
const savedId = (at: [number, number]) => `${at[0].toFixed(5)},${at[1].toFixed(5)}`;

function SaveButton({ selection, saved, onSave }: { selection: Selection; saved: Saved[]; onSave: (selection: Selection) => void }) {
  if (selection.kind !== 'building' && selection.kind !== 'receiver') return null;
  const isSaved = saved.some((s) => s.id === savedId(selection.at));
  const full = saved.length >= SAVED_MAX;
  return <button type="button" className="save-place" disabled={isSaved || full} onClick={() => onSave(selection)}>
    {isSaved ? 'Saved to compare ✓' : full ? `Compare list is full (${SAVED_MAX})` : 'Save to compare'}</button>;
}

function ModelNote({ model }: { model: string | null }) {
  if (!model) return null;
  return <p className="receiver-meta model-line">{model === 'county-v2'
    ? 'Computed with the upgraded model: 2023 lidar terrain, sound walls, roads on bridges.'
    : 'Computed with the earlier model (no sound walls, coarser terrain); this area is being recomputed.'}</p>;
}

function Inspector({ selection, period, onClose, covered, percentiles, modelAt, mappedKm2, saved, onSave }: { selection: Selection | null; period: Period; onClose: () => void; covered: (lng: number, lat: number) => boolean | null; percentiles?: Layers['building_percentiles']; modelAt: (lng: number, lat: number) => string | null; mappedKm2?: number; saved: Saved[]; onSave: (selection: Selection) => void }) {
  if (!selection) return <div className="inspection-empty"><strong>Select a place on the map</strong><p>Search an address above, or click any building, spot or road to see its day, evening, night and 24 h levels.{mappedKm2 ? ` About ${mappedKm2.toLocaleString()} km² are mapped so far, growing outward from Tarzana.` : ''}</p></div>;
  const close = <button type="button" className="plain-icon" aria-label="Close" onClick={onClose}>×</button>;
  if (selection.kind === 'receiver') {
    const value = selection.values[period];
    return <><div className="receiver-heading"><span>{selection.facade ? 'Building wall · 4 m up, 2 m out' : 'Open ground · 1.5 m up'}</span>{close}</div>
      <AddressLine at={selection.at} near={!selection.facade} />
      <div className="receiver-result"><strong>{selection.masked || value === null ? 'Unavailable' : value.toFixed(1)}</strong><span>{selection.masked || value === null ? '' : UNIT(period)}</span></div>
      {!selection.masked && <LevelWords value={value} period={period} />}
      {selection.masked ? <p className="receiver-meta">This point failed the physical plausibility check and is not shown as a value.</p> : <ValueRows values={selection.values} period={period} />}
      <Aircraft value={selection.aircraft} />
      {selection.onRoad && <p className="receiver-meta">Within 3 m of a road centerline: this is on the road, not a living location.</p>}
      <ModelNote model={modelAt(selection.at[0], selection.at[1])} />
      <SaveButton selection={selection} saved={saved} onSave={onSave} /></>;
  }
  if (selection.kind === 'building') {
    return <><div className="receiver-heading"><span>Building · about {selection.height.toFixed(0)} m tall</span>{close}</div>
      <AddressLine at={selection.at} known={selection.address} />
      <div className="receiver-result"><strong>{selection.values[period] === null ? '—' : selection.values[period]!.toFixed(1)}</strong><span>{period === 'Q' ? 'dB CNEL, loudest wall · 24 h' : `dB, loudest wall · ${PERIOD_NAME[period].toLowerCase()}`}</span></div>
      <LevelWords value={selection.values[period]} period={period} />
      <Compare value={selection.values[period]} period={period} percentiles={percentiles} />
      <ValueRows values={selection.values} period={period} /><Aircraft value={selection.aircraft} /><p className="receiver-meta">{selection.lowest[period] !== null && selection.values[period] !== null
        ? `Least exposed wall: ${selection.lowest[period]!.toFixed(1)} dB, ${(selection.values[period]! - selection.lowest[period]!).toFixed(0)} dB below the loudest (${selection.count} modeled points around the walls). Bedrooms on the quiet side hear less.`
        : `Loudest of ${selection.count} modeled points around the walls. The side facing away from traffic is often 10 dB or more below the loudest side.`} Switch to Dots to see each wall.</p>
      <ModelNote model={modelAt(selection.at[0], selection.at[1])} />
      <SaveButton selection={selection} saved={saved} onSave={onSave} /></>;
  }
  if (selection.kind === 'empty') {
    const inside = covered(selection.at[0], selection.at[1]);
    if (inside === false) return <><div className="receiver-heading"><span>Not modeled yet</span>{close}</div><AddressLine at={selection.at} near />
      <p className="level-words"><strong>No value here yet.</strong> This place is outside the area computed so far. The map grows outward from Tarzana as the county calculation runs; blank never means quiet.</p></>;
    return <><div className="receiver-heading"><span>Nothing modeled right here</span>{close}</div>
      <p className="level-words">Zoom in and click a building, or switch the display to Dots and click a point.</p></>;
  }
  if (selection.kind === 'road') {
    return <><div className="receiver-heading"><span>Road in the model</span>{close}</div>
      <div className="context-result"><strong>{selection.name}</strong><span>{selection.aadt.toLocaleString()} vehicles a day · {selection.basis === 'hpms' ? 'federal traffic count (FHWA HPMS 2024)' : 'typical value for this street type (no count)'}</span></div></>;
  }
  return <><div className="receiver-heading"><span>Context</span>{close}</div><div className="context-result"><strong>{selection.title}</strong><span>{selection.detail}</span></div></>;
}

export default function CountyMapPage() {
  const [ready, setReady] = useState(false);
  const [period, setPeriod] = useState<Period>('D');
  const [noise, setNoise] = useState<NoiseStyle>('field');
  const [mode3d, setMode3d] = useState(false);
  const [theme, setTheme] = useState<BaseTheme>('light');
  const [roads, setRoads] = useState(false);
  const [photo3d, setPhoto3d] = useState(false);
  const [splatStatus, setSplatStatus] = useState<'loading' | 'ready' | 'error' | 'off'>('off');
  const [context, setContext] = useState(CONTEXT_DEFAULTS);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [saved, setSaved] = useState<Saved[]>([]);
  const savedReady = useRef(false);
  const [status, setStatus] = useState<MapStatus>({ loading: true, error: null, zoom: 13 });
  const [layers, setLayers] = useState<Layers | null>(null);
  const [coverage, setCoverage] = useState<{ ring: Ring; model: string }[] | null>(null);
  const covered = useCallback((lng: number, lat: number) => (coverage ? coverage.some((tile) => insideRing(lng, lat, tile.ring)) : null), [coverage]);
  const modelAt = useCallback((lng: number, lat: number) => coverage?.find((tile) => insideRing(lng, lat, tile.ring))?.model ?? null, [coverage]);
  const [initialCamera, setInitialCamera] = useState<Camera | null>(null);
  const [target, setTarget] = useState<{ lng: number; lat: number; zoom?: number; pitch?: number; bearing?: number; nonce: number; select?: boolean; label?: string } | null>(null);
  const [query, setQuery] = useState('');
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const [searchFocused, setSearchFocused] = useState(false);
  const pickedRef = useRef('');
  const [message, setMessage] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [notice, setNotice] = useState('');
  const cameraRef = useRef<Camera | null>(null);

  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.slice(1));
    if (['D', 'E', 'N', 'Q'].includes(hash.get('period') ?? '')) setPeriod(hash.get('period') as Period);
    if (['field', 'bands', 'glow', 'dots'].includes(hash.get('style') ?? '')) setNoise(hash.get('style') as NoiseStyle);
    if (['light', 'dark', 'grayscale', 'satellite'].includes(hash.get('base') ?? '')) setTheme(hash.get('base') as BaseTheme);
    setMode3d(hash.get('mode') === '3d');
    setRoads(hash.get('roads') === '1');
    const on = new Set((hash.get('context') ?? '').split(',').filter(Boolean));
    setContext(Object.fromEntries(Object.keys(CONTEXT_DEFAULTS).map((id) => [id, on.has(id)])) as Record<ContextId, boolean>);
    const n = (k: string) => Number(hash.get(k));
    if (hash.has('lat') && hash.has('lng') && Math.abs(n('lat')) <= 85 && Math.abs(n('lng')) <= 180) {
      setInitialCamera({ lat: n('lat'), lng: n('lng'), zoom: Math.min(19, Math.max(9, n('z') || 14)), pitch: Math.min(78, Math.max(0, n('pitch') || 0)), bearing: n('bearing') || 0 });
    }
    // A shared link may carry the selected place (sel=lng,lat): select the building there once the map is up.
    const sel = (hash.get('sel') ?? '').split(',').map(Number);
    if (sel.length === 2 && sel.every(Number.isFinite) && Math.abs(sel[0]) <= 180 && Math.abs(sel[1]) <= 85) {
      setTarget({ lng: sel[0], lat: sel[1], zoom: Math.min(19, Math.max(15, n('z') || 17)), nonce: Date.now(), select: true });
    }
    try {
      const raw = window.localStorage.getItem(SAVED_KEY);
      const list = raw ? JSON.parse(raw) : [];
      if (Array.isArray(list)) setSaved(list.filter((s) => s && Array.isArray(s.at) && typeof s.label === 'string').slice(0, SAVED_MAX));
    } catch { /* storage blocked: the list just starts empty */ }
    savedReady.current = true;
    fetch(`${LAYERS_URL}layers.json`).then((r) => (r.ok ? r.json() : null)).then(setLayers).catch(() => setLayers(null));
    fetch(`${LAYERS_URL}coverage.geojson`).then((r) => (r.ok ? r.json() : null))
      .then((doc: { features?: { properties?: { model?: string }; geometry: { coordinates: Ring[] } }[] } | null) => setCoverage(doc?.features?.map((f) => ({ ring: f.geometry.coordinates[0], model: f.properties?.model ?? 'county-v1' })) ?? null))
      .catch(() => setCoverage(null));
    setReady(true);
  }, []);

  const selectedAt = selection?.kind === 'receiver' || selection?.kind === 'building' ? `${selection.at[0].toFixed(6)},${selection.at[1].toFixed(6)}` : '';
  const writeView = useCallback(() => {
    const c = cameraRef.current;
    if (!c) return;
    const hash = new URLSearchParams({ lat: c.lat.toFixed(5), lng: c.lng.toFixed(5), z: c.zoom.toFixed(2), pitch: c.pitch.toFixed(0), bearing: c.bearing.toFixed(0), period, style: noise, mode: mode3d ? '3d' : '2d', base: theme, roads: roads ? '1' : '0', context: Object.entries(context).filter(([, v]) => v).map(([k]) => k).join(',') });
    if (selectedAt) hash.set('sel', selectedAt);
    window.history.replaceState(null, '', `${window.location.pathname}#${hash}`);
  }, [period, noise, mode3d, theme, roads, context, selectedAt]);
  useEffect(() => {
    if (!savedReady.current) return;
    try { window.localStorage.setItem(SAVED_KEY, JSON.stringify(saved)); } catch { /* storage blocked */ }
  }, [saved]);
  const savePlace = useCallback(async (place: Selection) => {
    if (place.kind !== 'building' && place.kind !== 'receiver') return;
    const known = place.kind === 'building' ? place.address : undefined;
    let label = known ?? '';
    if (!label) { try { const found = await addressAt(place.at[0], place.at[1]); label = found ? `${found.near ? 'Near ' : ''}${found.text}` : ''; } catch { /* lookup failed */ } }
    if (!label) label = `Place at ${place.at[1].toFixed(4)}, ${place.at[0].toFixed(4)}`;
    setSaved((list) => (list.some((s) => s.id === savedId(place.at)) || list.length >= SAVED_MAX ? list : [...list, { id: savedId(place.at), at: place.at, label, values: place.values }]));
  }, []);
  // Escape closes the selected place (unless typing in the search box).
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape' && !(event.target instanceof HTMLInputElement)) setSelection(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  // Show "Loading map…" only when loading takes a moment, not on every pan.
  const [slowLoading, setSlowLoading] = useState(false);
  useEffect(() => {
    if (!status.loading) { setSlowLoading(false); return; }
    const timer = window.setTimeout(() => setSlowLoading(true), 1200);
    return () => window.clearTimeout(timer);
  }, [status.loading]);
  useEffect(() => { writeView(); }, [writeView]);
  const onCamera = useCallback((camera: Camera) => { cameraRef.current = camera; writeView(); }, [writeView]);

  // Address suggestions while typing (needs a few characters; a picked suggestion is not re-suggested).
  useEffect(() => {
    const text = query.trim();
    if (!searchFocused || text.length < 3 || text === pickedRef.current || /^-?\d+(\.\d+)?\s*,/.test(text)) { setSuggestions([]); return; }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      suggestAddresses(text, controller.signal).then((list) => { setSuggestions(list); setActiveSuggestion(-1); }).catch(() => setSuggestions([]));
    }, 250);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [query, searchFocused]);

  function pickSuggestion(suggestion: Suggestion) {
    pickedRef.current = suggestion.text;
    setQuery(suggestion.text);
    setSuggestions([]);
    void runSearch(suggestion.text, suggestion.magicKey);
  }

  function searchKeys(event: KeyboardEvent<HTMLInputElement>) {
    if (!suggestions.length) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveSuggestion((i) => (i + (event.key === 'ArrowDown' ? 1 : -1) + suggestions.length) % suggestions.length);
    } else if (event.key === 'Enter' && activeSuggestion >= 0) {
      event.preventDefault();
      pickSuggestion(suggestions[activeSuggestion]);
    } else if (event.key === 'Escape') {
      setSuggestions([]);
    }
  }

  async function search(event: React.FormEvent) {
    event.preventDefault();
    setSuggestions([]);
    await runSearch(query);
  }

  async function runSearch(raw: string, magicKey?: string) {
    const text = raw.trim().toLowerCase();
    const pair = text.match(/^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/);
    let place = PLACES[text] ?? (pair ? [Number(pair[2]), Number(pair[1])] as [number, number] : null);
    let zoom = 15;
    let select = false;
    let label: string | undefined;
    if (!place && text) {
      setMessage('Looking up the address…');
      try {
        const found = await findAddress(raw.trim(), undefined, magicKey);
        // A house number means one property: zoom in and select it. A bare street or place stays wider.
        if (found) { place = [found.lng, found.lat]; select = /^\d/.test(found.label); zoom = select ? 18 : 16; label = found.label.replace(/, CA, (\d{5})$/, ', CA $1'); pickedRef.current = found.label; setQuery(found.label); }
        if (found && covered(found.lng, found.lat) === false) {
          setMessage('This address is outside the area modeled so far, so there is no value yet. The map grows as the county calculation runs.');
          setTarget({ lng: found.lng, lat: found.lat, zoom: 15, nonce: Date.now() });
          setSelection({ kind: 'empty', at: [found.lng, found.lat] });
          setExpanded(false);
          return;
        }
      } catch { setMessage('The address service is unavailable right now. Try a place name or latitude, longitude.'); return; }
    }
    if (!place || Math.abs(place[0]) > 180 || Math.abs(place[1]) > 85) { setMessage('No LA County address matched. Try a street address with city, a place such as Reseda, or latitude, longitude.'); return; }
    setTarget({ lng: place[0], lat: place[1], zoom, nonce: Date.now(), select, label });
    setMessage(''); setExpanded(false);
  }
  async function copyView() {
    writeView();
    // Phones: the system share sheet; elsewhere: copy to the clipboard.
    if (navigator.share && window.matchMedia('(pointer: coarse)').matches) {
      try { await navigator.share({ title: 'Quiet LA', url: window.location.href }); return; } catch { /* cancelled: fall back to copying */ }
    }
    try { await navigator.clipboard.writeText(window.location.href); setNotice('Link copied.'); } catch { setNotice('Your view is in the address bar.'); }
  }
  const selectedKey = selection?.kind === 'receiver' || selection?.kind === 'building' ? selection.key : null;

  if (!ready) return <div className="workspace-boot" role="status">Opening Quiet LA…</div>;
  return <main className={`workspace county-workspace ${expanded ? 'is-expanded' : ''}`}>
    <header className="workspace-header">
      <a href="/map/" className="workspace-brand" aria-label="Quiet LA home">Quiet LA<span>Noise explorer</span></a>
      <div className="workspace-scope">Los Angeles County <span>· road and aircraft noise · modeled preview</span></div>
      <Choice label="Map dimension" value={mode3d ? '3d' : '2d'} options={[['2d', '2D'], ['3d', '3D']]} onChange={(m) => setMode3d(m === '3d')} />
    </header>
    <CountyMap layersUrl={LAYERS_URL} period={period} noise={noise} mode3d={mode3d} roads={roads} context={context} theme={theme} photo3d={photo3d && mode3d} splatSceneUrl={SPLAT_SCENE} onSplatStatus={setSplatStatus} target={target} initialCamera={initialCamera} selectedKey={selectedKey} onSelect={setSelection} onStatus={setStatus} onCamera={onCamera} />
    <aside className="map-guide" aria-label="Map controls and inspection">
      <div className="guide-heading"><h1>How loud is it here?</h1><button type="button" className="sheet-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Less' : 'Controls'}</button></div>
      <p className="guide-intro">Modeled noise outside every home, from freeways down to residential streets. The 24 h view adds aircraft; helicopters and sirens are not included yet.</p>
      <form className="place-search" onSubmit={search} role="search">
        <label htmlFor="place-search">Look up an address</label>
        <div><input id="place-search" value={query} onChange={(e) => { pickedRef.current = ''; setQuery(e.target.value); }} onKeyDown={searchKeys}
          onFocus={() => setSearchFocused(true)} onBlur={() => window.setTimeout(() => setSearchFocused(false), 150)}
          placeholder="e.g. 5518 Aura Ave, Tarzana" autoComplete="off" role="combobox" aria-expanded={suggestions.length > 0} aria-controls="place-suggestions" aria-autocomplete="list"
          aria-activedescendant={activeSuggestion >= 0 ? `place-suggestion-${activeSuggestion}` : undefined} /><button type="submit" aria-label="Find address">→</button></div>
        {suggestions.length > 0 && <ul className="search-suggestions" id="place-suggestions" role="listbox">{suggestions.map((s, i) => (
          <li key={s.magicKey + s.text} id={`place-suggestion-${i}`} role="option" aria-selected={i === activeSuggestion} onMouseDown={(e) => { e.preventDefault(); pickSuggestion(s); }}>{s.text}</li>))}</ul>}
        <p role="status">{message}</p>
      </form>
      <div className="quick-controls"><Choice label="Time of day" value={period} options={[['D', 'Day'], ['E', 'Evening'], ['N', 'Night'], ['Q', '24 h']]} onChange={setPeriod} /></div>
      <section className="receiver-section" aria-live="polite"><Inspector selection={selection} period={period} onClose={() => setSelection(null)} covered={covered} percentiles={layers?.building_percentiles} modelAt={modelAt} mappedKm2={layers?.tiles.length} saved={saved} onSave={savePlace} /></section>
      {saved.length > 0 && <section className="saved-places" aria-label="Saved places">
        <h2>Compare saved places <span>{PERIOD_NAME[period].toLowerCase()}{period === 'Q' ? ' CNEL' : ''}</span></h2>
        {saved.map((place) => <div className="saved-row" key={place.id}>
          <button type="button" className="saved-go" onClick={() => setTarget({ lng: place.at[0], lat: place.at[1], zoom: 18, nonce: Date.now(), select: true, label: place.label.startsWith('Near ') || place.label.startsWith('Place at') ? undefined : place.label })}>
            <i style={{ background: band(place.values[period]) ?? '#9ea3a8' }} /><span>{place.label}</span><strong>{place.values[period] === null ? '—' : `${place.values[period]!.toFixed(1)} dB`}</strong></button>
          <button type="button" className="plain-icon" aria-label={`Remove ${place.label}`} onClick={() => setSaved((list) => list.filter((s) => s.id !== place.id))}>×</button>
        </div>)}
        <p className="control-help">Loudest wall for buildings. Saved in this browser only.</p>
      </section>}
      <div className="guide-body">
        <section className="guide-section"><h2>Noise display</h2><Choice label="Noise display style" value={noise} options={[['field', 'Field'], ['bands', 'Bands'], ['glow', 'Glow'], ['dots', 'Dots']]} onChange={setNoise} /><p className="control-help">{STYLE_HELP[noise]}{mode3d ? ' In 3D, buildings are colored by their loudest wall.' : ''}</p></section>
        <section className="guide-section"><h2>Base map</h2><Choice label="Base map" value={theme} options={[['light', 'Light'], ['grayscale', 'Gray'], ['dark', 'Dark'], ['satellite', 'Photo']]} onChange={setTheme} /></section>
        <section className="guide-section"><h2>Photo 3D <span className="county-beta">beta</span></h2>
          <label className="source-toggle"><input type="checkbox" checked={photo3d} onChange={(e) => { const on = e.target.checked; setPhoto3d(on); if (on) { setMode3d(true); setTarget({ ...SPLAT_VIEW, nonce: Date.now() }); } }} />Photo-real 3D showcase: Ventura Blvd and the 101</label>
          <p className="control-help">{splatStatus === 'loading' ? 'Loading about 20 MB of 3D scan…' : splatStatus === 'error' ? 'The 3D scan could not load.' : 'Built from the 2023 USGS laser scan and 2022 aerial photos. Rooftops and trees are sharp; building sides are soft because aerial scans see little of walls.'}</p>
        </section>
        <section className="guide-section"><h2>Layers</h2>
          <label className="source-toggle"><input type="checkbox" checked={roads} onChange={(e) => setRoads(e.target.checked)} />Roads in the model, by traffic</label>
          <div className="context-options">{(Object.keys(CONTEXT_LABELS) as ContextId[]).map((id) => <label key={id} className={`context-toggle context-${id}`}><input type="checkbox" checked={context[id]} onChange={(e) => { setContext({ ...context, [id]: e.target.checked }); if (id === 'airport-contours' && e.target.checked) setPeriod('Q'); }} />{CONTEXT_LABELS[id]}</label>)}</div>
          {context['airport-contours'] && <p className="control-help">Lines are the official airport contours of CNEL, a 24-hour average that counts evening noise 5 dB and night noise 10 dB louder. The 24 h view adds aircraft to the road noise: levels between the lines are interpolated, and because official maps stop at 65 CNEL, each airport&rsquo;s contours are extended to 55 CNEL as an estimate. Contours older than 2000 (Compton, El Monte, Torrance, Palmdale, Agua Dulce, Catalina) are drawn but not counted.</p>}
        </section>
        <div className="guide-actions"><button type="button" onClick={copyView}>{selectedAt ? 'Copy link to this place' : 'Copy link to this view'}</button></div>
      </div>
      <div className="guide-secondary">
        <details className="study-details"><summary>Coverage & method</summary>
          <p><strong>{layers ? `${layers.tiles.length} km² modeled, ${layers.receiver_count.toLocaleString()} points, ${layers.building_count.toLocaleString()} buildings.` : 'Coverage is loading.'}</strong> More of the county is added as the calculation runs. Dashed lines mark the modeled area; anything outside it is not modeled yet, not quiet.</p>
          <p>CNOSSOS-EU road noise (NoiseModelling 6), every Census road with FHWA HPMS 2024 traffic counts where they exist and typical values elsewhere, LA County building footprints and heights. Tiles are being recomputed one by one with the 2023 USGS lidar terrain at 10 m, freeway sound walls found in the lidar, and roads on bridges at deck height; tiles not yet redone use 10 m USGS terrain without walls. Sound bends over roofs, hills and walls; reflections between buildings are not yet included. The 24 h view (CNEL) adds aircraft estimated from the official airport contours. Evening traffic is 0.6× and night 0.2× the daytime hourly flow. Values are modeled and uncalibrated: not measurements and not indoor levels.</p>
          {layers && <p>Data built {layers.built_at_utc.replace('T', ' ').replace('Z', ' UTC')}.</p>}
        </details>
        <p className="workspace-notice" role="status">{notice}</p>
      </div>
    </aside>
    <div className="workspace-legend county-legend" aria-label="Map legend">
      <div><span>{period === 'Q' ? 'Roads + aircraft · 24 h · dB CNEL' : `Road noise · ${PERIOD_NAME[period]} · dB LAeq`}</span></div>
      <div className="county-legend-bar" aria-hidden="true">{BAND_COLORS.map((c) => <i key={c} style={{ background: c }} />)}</div>
      <div className="county-legend-ticks" aria-hidden="true">{BAND_EDGES.map((e) => <span key={e}>{e}</span>)}</div>
      <div className="county-legend-words" aria-hidden="true"><span>quieter</span><span>louder</span></div>
      <p>WHO guideline for road traffic: 53 dB Lden, 45 dB at night. Blank areas are not modeled yet, not quiet.</p>
    </div>
    {(status.error || slowLoading) && <div className={`workspace-state ${status.error ? 'is-error' : ''}`} role={status.error ? 'alert' : 'status'}>{status.error ? `Map data could not load: ${status.error}` : 'Loading map…'}</div>}
  </main>;
}
