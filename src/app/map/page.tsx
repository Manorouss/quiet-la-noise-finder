'use client';

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import CountyMap, { type Camera, type MapStatus, type Selection, type Values } from '@/components/CountyMap';
import { BAND_COLORS, BAND_EDGES, type BaseTheme, type ContextId, type NoiseStyle, type Period } from '@/lib/county-map-style';
import { addressAt, findAddress, type Address } from '@/lib/address';

const LAYERS_URL = process.env.NEXT_PUBLIC_QUIET_LA_LAYERS_URL || '/county-layers/';
// Photo 3D showcase (lidar + aerial imagery as Gaussian splats): Studio City, Ventura Blvd and US-101.
const SPLAT_SCENE = `${LAYERS_URL}splats/showcase_101_ventura`;
const SPLAT_VIEW = { lng: -118.3721, lat: 34.1474, zoom: 17.3, pitch: 62, bearing: -35 };
const PERIOD_NAME: Record<Period, string> = { D: 'Day', E: 'Evening', N: 'Night' };
const CONTEXT_DEFAULTS: Record<ContextId, boolean> = { 'airport-contours': false, heliports: false, 'county-fire': false, 'city-fire': false };
const CONTEXT_LABELS: Record<ContextId, string> = { 'airport-contours': 'Airport noise contours (official CNEL)', heliports: 'Heliports', 'county-fire': 'LA County fire stations', 'city-fire': 'City of LA fire stations' };
const STYLE_HELP: Record<NoiseStyle, string> = {
  field: 'A smooth surface interpolated from the modeled points, 5 m detail.',
  bands: 'The same surface in 5 dB steps, like an official noise map.',
  glow: 'Only the louder places glow; quieter areas stay clear.',
  dots: 'Every modeled point: building walls (larger) and open ground.',
};
const PLACES: Record<string, [number, number]> = { tarzana: [-118.553, 34.172], reseda: [-118.536, 34.201], 'van nuys': [-118.449, 34.186], northridge: [-118.536, 34.228], encino: [-118.501, 34.159], 'lake balboa': [-118.497, 34.197] };
type Layers = { tiles: string[]; receiver_count: number; building_count: number; built_at_utc: string };

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
  return <div className="county-values">{(['D', 'E', 'N'] as Period[]).map((p) => <div key={p} className={p === period ? 'is-active' : ''}><span>{PERIOD_NAME[p]}</span><i style={{ background: band(values[p]) ?? '#9ea3a8' }} /><strong>{values[p] === null ? '—' : `${values[p]!.toFixed(1)} dB`}</strong></div>)}</div>;
}

function AddressLine({ at, near }: { at: [number, number]; near?: boolean }) {
  const key = `${at[0]},${at[1]}`;
  const [result, setResult] = useState<{ key: string; address: Address | null; failed: boolean } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    addressAt(at[0], at[1], controller.signal)
      .then((address) => setResult({ key, address, failed: false }))
      .catch(() => { if (!controller.signal.aborted) setResult({ key, address: null, failed: true }); });
    return () => controller.abort();
  }, [key, at]);
  const current = result?.key === key ? result : null;
  if (!current) return <p className="selection-address is-pending">Finding the address…</p>;
  if (!current.address) return <p className="selection-address is-pending">{current.failed ? 'Address lookup is unavailable right now.' : 'No street address on record here.'}</p>;
  return <p className="selection-address">{near || current.address.near ? <span>Near </span> : null}{current.address.text}</p>;
}

function Inspector({ selection, period, onClose }: { selection: Selection | null; period: Period; onClose: () => void }) {
  if (!selection) return <div className="inspection-empty"><strong>Select a place on the map</strong><p>Click any modeled spot, building or road to see its modeled day, evening and night levels.</p></div>;
  const close = <button type="button" className="plain-icon" aria-label="Close" onClick={onClose}>×</button>;
  if (selection.kind === 'receiver') {
    const value = selection.values[period];
    return <><div className="receiver-heading"><span>{selection.facade ? 'Building wall · 4 m up, 2 m out' : 'Open ground · 1.5 m up'}</span>{close}</div>
      <AddressLine at={selection.at} near={!selection.facade} />
      <div className="receiver-result"><strong>{selection.masked || value === null ? 'Unavailable' : value.toFixed(1)}</strong><span>{selection.masked || value === null ? '' : `dB LAeq · ${PERIOD_NAME[period].toLowerCase()}`}</span></div>
      {selection.masked ? <p className="receiver-meta">This point failed the physical plausibility check and is not shown as a value.</p> : <ValueRows values={selection.values} period={period} />}
      {selection.onRoad && <p className="receiver-meta">Within 3 m of a road centerline: this is on the road, not a living location.</p>}</>;
  }
  if (selection.kind === 'building') {
    return <><div className="receiver-heading"><span>Building · about {selection.height.toFixed(0)} m tall</span>{close}</div>
      <AddressLine at={selection.at} />
      <div className="receiver-result"><strong>{selection.values[period] === null ? '—' : selection.values[period]!.toFixed(1)}</strong><span>dB, loudest wall · {PERIOD_NAME[period].toLowerCase()}</span></div>
      <ValueRows values={selection.values} period={period} /><p className="receiver-meta">Loudest of {selection.count} modeled points around the walls. The quietest side of a building is often 10 dB or more below its loudest side. Switch to Dots to see each wall.</p></>;
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
  const [status, setStatus] = useState<MapStatus>({ loading: true, error: null, zoom: 13 });
  const [layers, setLayers] = useState<Layers | null>(null);
  const [initialCamera, setInitialCamera] = useState<Camera | null>(null);
  const [target, setTarget] = useState<{ lng: number; lat: number; zoom?: number; pitch?: number; bearing?: number; nonce: number } | null>(null);
  const [query, setQuery] = useState('');
  const [message, setMessage] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [notice, setNotice] = useState('');
  const cameraRef = useRef<Camera | null>(null);

  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.slice(1));
    if (['D', 'E', 'N'].includes(hash.get('period') ?? '')) setPeriod(hash.get('period') as Period);
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
    fetch(`${LAYERS_URL}layers.json`).then((r) => (r.ok ? r.json() : null)).then(setLayers).catch(() => setLayers(null));
    setReady(true);
  }, []);

  const writeView = useCallback(() => {
    const c = cameraRef.current;
    if (!c) return;
    const hash = new URLSearchParams({ lat: c.lat.toFixed(5), lng: c.lng.toFixed(5), z: c.zoom.toFixed(2), pitch: c.pitch.toFixed(0), bearing: c.bearing.toFixed(0), period, style: noise, mode: mode3d ? '3d' : '2d', base: theme, roads: roads ? '1' : '0', context: Object.entries(context).filter(([, v]) => v).map(([k]) => k).join(',') });
    window.history.replaceState(null, '', `${window.location.pathname}#${hash}`);
  }, [period, noise, mode3d, theme, roads, context]);
  useEffect(() => { writeView(); }, [writeView]);
  const onCamera = useCallback((camera: Camera) => { cameraRef.current = camera; writeView(); }, [writeView]);

  async function search(event: React.FormEvent) {
    event.preventDefault();
    const text = query.trim().toLowerCase();
    const pair = text.match(/^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/);
    let place = PLACES[text] ?? (pair ? [Number(pair[2]), Number(pair[1])] as [number, number] : null);
    let zoom = 15;
    if (!place && text) {
      setMessage('Looking up the address…');
      try {
        const found = await findAddress(query.trim());
        if (found) { place = [found.lng, found.lat]; zoom = 18; setQuery(found.label); }
      } catch { setMessage('The address service is unavailable right now. Try a place name or latitude, longitude.'); return; }
    }
    if (!place || Math.abs(place[0]) > 180 || Math.abs(place[1]) > 85) { setMessage('No LA County address matched. Try a street address with city, a place such as Reseda, or latitude, longitude.'); return; }
    setTarget({ lng: place[0], lat: place[1], zoom, nonce: Date.now() });
    setMessage(''); setExpanded(false);
  }
  async function copyView() {
    writeView();
    try { await navigator.clipboard.writeText(window.location.href); setNotice('View link copied.'); } catch { setNotice('Your view is in the address bar.'); }
  }
  const selectedKey = selection?.kind === 'receiver' || selection?.kind === 'building' ? selection.key : null;

  if (!ready) return <div className="workspace-boot" role="status">Opening Quiet LA…</div>;
  return <main className={`workspace county-workspace ${expanded ? 'is-expanded' : ''}`}>
    <header className="workspace-header">
      <a href="/map/" className="workspace-brand" aria-label="Quiet LA home">Quiet LA<span>Noise explorer</span></a>
      <div className="workspace-scope">Los Angeles County <span>· road noise, county model v1 · preview</span></div>
      <Choice label="Map dimension" value={mode3d ? '3d' : '2d'} options={[['2d', '2D'], ['3d', '3D']]} onChange={(m) => setMode3d(m === '3d')} />
    </header>
    <CountyMap layersUrl={LAYERS_URL} period={period} noise={noise} mode3d={mode3d} roads={roads} context={context} theme={theme} photo3d={photo3d && mode3d} splatSceneUrl={SPLAT_SCENE} onSplatStatus={setSplatStatus} target={target} initialCamera={initialCamera} selectedKey={selectedKey} onSelect={setSelection} onStatus={setStatus} onCamera={onCamera} />
    <aside className="map-guide" aria-label="Map controls and inspection">
      <div className="guide-heading"><h1>How loud is it here?</h1><button type="button" className="sheet-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Less' : 'Controls'}</button></div>
      <p className="guide-intro">Modeled road noise outside homes, from freeways down to residential streets. Aircraft, helicopters and sirens are not included yet.</p>
      <div className="quick-controls"><Choice label="Time of day" value={period} options={[['D', 'Day'], ['E', 'Evening'], ['N', 'Night']]} onChange={setPeriod} /></div>
      <div className="guide-body">
        <form className="place-search" onSubmit={search}><label htmlFor="place-search">Go to an address or place</label><div><input id="place-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Street address, Reseda, or lat, lng" autoComplete="off" /><button type="submit" aria-label="Find place">→</button></div><p role="status">{message}</p></form>
        <section className="guide-section"><h2>Noise display</h2><Choice label="Noise display style" value={noise} options={[['field', 'Field'], ['bands', 'Bands'], ['glow', 'Glow'], ['dots', 'Dots']]} onChange={setNoise} /><p className="control-help">{STYLE_HELP[noise]}{mode3d ? ' In 3D, buildings are colored by their loudest wall.' : ''}</p></section>
        <section className="guide-section"><h2>Base map</h2><Choice label="Base map" value={theme} options={[['light', 'Light'], ['grayscale', 'Gray'], ['dark', 'Dark'], ['satellite', 'Photo']]} onChange={setTheme} /></section>
        <section className="guide-section"><h2>Photo 3D <span className="county-beta">beta</span></h2>
          <label className="source-toggle"><input type="checkbox" checked={photo3d} onChange={(e) => { const on = e.target.checked; setPhoto3d(on); if (on) { setMode3d(true); setTarget({ ...SPLAT_VIEW, nonce: Date.now() }); } }} />Photo-real 3D showcase: Ventura Blvd and the 101</label>
          <p className="control-help">{splatStatus === 'loading' ? 'Loading about 20 MB of 3D scan…' : splatStatus === 'error' ? 'The 3D scan could not load.' : 'Built from the 2023 USGS laser scan and 2022 aerial photos. Rooftops and trees are sharp; building sides are soft because aerial scans see little of walls.'}</p>
        </section>
        <section className="guide-section"><h2>Layers</h2>
          <label className="source-toggle"><input type="checkbox" checked={roads} onChange={(e) => setRoads(e.target.checked)} />Roads in the model, by traffic</label>
          <div className="context-options">{(Object.keys(CONTEXT_LABELS) as ContextId[]).map((id) => <label key={id} className={`context-toggle context-${id}`}><input type="checkbox" checked={context[id]} onChange={(e) => setContext({ ...context, [id]: e.target.checked })} />{CONTEXT_LABELS[id]}</label>)}</div>
        </section>
        <div className="guide-actions"><button type="button" onClick={copyView}>Copy view link</button></div>
      </div>
      <section className="receiver-section" aria-live="polite"><Inspector selection={selection} period={period} onClose={() => setSelection(null)} /></section>
      <div className="guide-secondary">
        <details className="study-details"><summary>Coverage & method</summary>
          <p><strong>{layers ? `${layers.tiles.length} km² modeled, ${layers.receiver_count.toLocaleString()} points, ${layers.building_count.toLocaleString()} buildings.` : 'Coverage is loading.'}</strong> More of the county is added as the calculation runs. Dashed lines mark the modeled area; anything outside it is not modeled yet, not quiet.</p>
          <p>CNOSSOS-EU road noise (NoiseModelling 6), every Census road with FHWA HPMS 2024 traffic counts where they exist and typical values elsewhere, LA County building footprints and heights, USGS terrain. Sound bends over roofs and hills; reflections between buildings are not yet included. Evening traffic is 0.6× and night 0.2× the daytime hourly flow. Values are modeled and uncalibrated: not measurements and not indoor levels.</p>
          {layers && <p>Data built {layers.built_at_utc.replace('T', ' ').replace('Z', ' UTC')}.</p>}
        </details>
        <p className="workspace-notice" role="status">{notice}</p>
      </div>
    </aside>
    <div className="workspace-legend county-legend" aria-label="Map legend">
      <div><span>Road noise · {PERIOD_NAME[period]} · dB LAeq</span></div>
      <div className="county-legend-bar" aria-hidden="true">{BAND_COLORS.map((c) => <i key={c} style={{ background: c }} />)}</div>
      <div className="county-legend-ticks" aria-hidden="true">{BAND_EDGES.map((e) => <span key={e}>{e}</span>)}</div>
      <p>WHO guideline for road traffic: 53 dB Lden, 45 dB at night. Blank areas are not modeled yet, not quiet.</p>
    </div>
    {(status.error || status.loading) && <div className={`workspace-state ${status.error ? 'is-error' : ''}`} role={status.error ? 'alert' : 'status'}>{status.error ? `Map data could not load: ${status.error}` : 'Loading map…'}</div>}
  </main>;
}
