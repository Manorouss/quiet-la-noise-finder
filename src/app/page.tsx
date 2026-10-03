'use client';

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import Link from 'next/link';
import DenseMap, { type ContextSelection, type DenseRecord, type DenseStatus, type Camera } from '@/components/DenseMap';
import type { ContextLayerId, ContextVisibility } from '@/lib/dense-map';
import PolicyPortal from '@/components/PolicyPortal';
import PilotPortal from '@/components/PilotPortal';
import { resolveRuntimeProfile } from '@/lib/runtime-profile.js';

type Period = 'D' | 'E' | 'N';
type Scenario = '23' | '585';
type Treatment = 'field' | 'bands' | 'dots' | 'glow';
type Saved = { id: number; name: string; record: DenseRecord; model: string };
const CONTEXT_DEFAULTS: ContextVisibility = { 'county-fire': false, 'city-fire': false, heliports: false, 'airport-contours': false };
const CONTEXT_LABELS: Record<ContextLayerId, string> = { 'county-fire': 'LA County fire stations', 'city-fire': 'City fire stations', heliports: 'Heliports', 'airport-contours': 'Airport noise contours' };
const MODEL = 'dense-v21-229981';
const STORAGE_KEY = 'quiet-la-dense-saved-v1';
const profile = resolveRuntimeProfile(process.env.NEXT_PUBLIC_QUIET_LA_DATA_PROFILE);
const local = profile.mode === 'local';
const pilotOnly = profile.id === 'pilot_v1';
const periodName = { D: 'Day', E: 'Evening', N: 'Night' };
const valueFor = (record: DenseRecord, scenario: Scenario, period: Period) => record[(scenario === '23' ? 4 : 7) + ['D', 'E', 'N'].indexOf(period)];

function Icon({ name }: { name: 'search' | 'close' | 'chevron' | 'bookmark' | 'share' }) {
  const paths = { search: 'm16 16 5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0', close: 'm6 6 12 12M6 18 18 6', chevron: 'm5 9 7 7 7-7', bookmark: 'M6 3h12v18l-6-4-6 4V3Z', share: 'M14 3h7v7m0-7L10 14M10 5H4v16h16v-6' };
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

function Choice<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: readonly (readonly [T, string])[]; onChange: (value: T) => void }) {
  function keys(event: KeyboardEvent<HTMLDivElement>) {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const index = options.findIndex(([id]) => id === (event.target as HTMLButtonElement).value);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + options.length) % options.length;
    onChange(options[next][0]);
    event.currentTarget.querySelectorAll<HTMLButtonElement>('button')[next]?.focus();
  }
  return <div className="choice" role="radiogroup" aria-label={label} onKeyDown={keys}>{options.map(([id, text]) => <button type="button" key={id} value={id} role="radio" aria-checked={value === id} tabIndex={value === id ? 0 : -1} onClick={() => onChange(id)}>{text}</button>)}</div>;
}

function DenseWorkspace() {
  const [ready, setReady] = useState(false);
  const [pilot, setPilot] = useState(false);
  const [period, setPeriod] = useState<Period>('D');
  const [scenario, setScenario] = useState<Scenario>('585');
  const [treatment, setTreatment] = useState<Treatment>('field');
  const [mode3d, setMode3d] = useState(false);
  const [sourcesVisible, setSourcesVisible] = useState(false);
  const [contextVisibility, setContextVisibility] = useState<ContextVisibility>(CONTEXT_DEFAULTS);
  const [contextSelection, setContextSelection] = useState<ContextSelection | null>(null);
  const [selected, setSelected] = useState<DenseRecord | null>(null);
  const [distance, setDistance] = useState(0);
  const [hasInspected, setHasInspected] = useState(false);
  const [fitRequest, setFitRequest] = useState(0);
  const [fitContextRequest, setFitContextRequest] = useState(0);
  const [target, setTarget] = useState<{ lng: number; lat: number; nonce: number; zoom?: number } | null>(null);
  const [status, setStatus] = useState<DenseStatus>({ loading: true, error: null, visibleCount: 0, detail: 'overview', inCoverage: true, threeDLoading: false });
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState('');
  const [searchMessage, setSearchMessage] = useState('');
  const [saved, setSaved] = useState<Saved[]>([]);
  const [storageReady, setStorageReady] = useState(false);
  const [notice, setNotice] = useState('');
  const cameraRef = useRef<Camera | null>(null);
  const receiverRef = useRef<HTMLElement | null>(null);
  const settingsRef = useRef({ period, scenario, treatment, mode3d, sourcesVisible, contextVisibility });
  settingsRef.current = { period, scenario, treatment, mode3d, sourcesVisible, contextVisibility };

  useEffect(() => {
    setPilot(new URLSearchParams(window.location.search).get('study') === 'pilot');
    const hash = new URLSearchParams(window.location.hash.slice(1));
    if (['D', 'E', 'N'].includes(hash.get('period') ?? '')) setPeriod(hash.get('period') as Period);
    if (['23', '585'].includes(hash.get('scenario') ?? '')) setScenario(hash.get('scenario') as Scenario);
    if (['field', 'bands', 'dots', 'glow'].includes(hash.get('style') ?? '')) setTreatment(hash.get('style') as Treatment);
    setMode3d(hash.get('mode') === '3d');
    setSourcesVisible(hash.get('sources') === '1');
    const context = new Set((hash.get('context') ?? '').split(',').filter(Boolean));
    setContextVisibility(Object.fromEntries(Object.keys(CONTEXT_DEFAULTS).map((id) => [id, context.has(id)])) as ContextVisibility);
    const lat = Number(hash.get('lat')); const lng = Number(hash.get('lng'));
    if (hash.has('lat') && hash.has('lng') && Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 85 && Math.abs(lng) <= 180) setTarget({ lat, lng, nonce: 1, zoom: hash.has('z') && Number.isFinite(Number(hash.get('z'))) ? Math.max(7, Math.min(18, Number(hash.get('z')))) : undefined });
    try {
      const stored: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
      if (Array.isArray(stored)) setSaved(stored.filter((item) => item?.model === MODEL && typeof item.name === 'string' && item.name.length <= 80 && Array.isArray(item.record) && item.record.length === 10 && item.record.every((v: unknown) => typeof v === 'number' && Number.isFinite(v)) && item.id === item.record[0]).slice(0, 6));
    } catch { setNotice('Saved receivers could not be restored on this device.'); }
    setStorageReady(true); setReady(true);
  }, []);

  useEffect(() => {
    if (!storageReady) return;
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(saved)); }
    catch { setNotice('Browser storage is unavailable. These saves will last for this visit.'); }
  }, [saved, storageReady]);

  const writeView = useCallback((camera?: Camera) => {
    if (camera) cameraRef.current = camera;
    const position = cameraRef.current;
    if (!position) return;
    const current = settingsRef.current;
    const hash = new URLSearchParams({ lat: position.lat.toFixed(6), lng: position.lng.toFixed(6), z: position.zoom.toFixed(2), mode: current.mode3d ? '3d' : '2d', period: current.period, scenario: current.scenario, style: current.treatment, sources: current.sourcesVisible ? '1' : '0', context: Object.entries(current.contextVisibility).filter(([, visible]) => visible).map(([id]) => id).join(',') });
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#${hash}`);
  }, []);
  useEffect(() => { if (ready) writeView(); }, [ready, period, scenario, treatment, mode3d, sourcesVisible, contextVisibility, writeView]);
  const inspect = useCallback((record: DenseRecord | null, distanceM: number) => { setContextSelection(null); setSelected(record); setDistance(distanceM); setHasInspected(true); if (window.innerWidth > 720) requestAnimationFrame(() => receiverRef.current?.scrollIntoView({ block: 'nearest' })); }, []);
  const inspectContext = useCallback((selection: ContextSelection | null) => { setSelected(null); setContextSelection(selection); setHasInspected(true); if (selection && window.innerWidth > 720) requestAnimationFrame(() => receiverRef.current?.scrollIntoView({ block: 'nearest' })); }, []);
  const fallback = useCallback(() => { setMode3d(false); }, []);

  function search(event: React.FormEvent) {
    event.preventDefault();
    const text = query.trim().toLowerCase();
    const presets: Record<string, [number, number]> = { tarzana: [-118.5676, 34.1705], 'western coverage': [-118.591, 34.17], 'eastern coverage': [-118.545, 34.17] };
    const pair = text.match(/^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/);
    const coordinates = presets[text] ?? (pair ? [Number(pair[2]), Number(pair[1])] : null);
    if (!coordinates || Math.abs(coordinates[0]) > 180 || Math.abs(coordinates[1]) > 85) { setSearchMessage('Try Tarzana, eastern coverage, western coverage, or latitude, longitude. Street-address search is not connected yet.'); return; }
    setTarget({ lng: coordinates[0], lat: coordinates[1], nonce: Date.now() });
    setSelected(null); setHasInspected(false); setSearchMessage('Location found. Select the map to inspect a modeled receiver.'); setExpanded(false);
  }
  function saveReceiver() {
    if (!selected) return;
    if (saved.some((item) => item.id === selected[0])) { setNotice('This receiver is already saved.'); return; }
    if (saved.length >= 6) { setNotice('You can compare six receivers. Remove one to save another.'); return; }
    setSaved((items) => [...items, { id: selected[0], name: `Receiver ${selected[0]}`, record: [...selected], model: MODEL }]);
    setNotice('Receiver saved on this device.');
  }
  async function copyView() {
    writeView();
    try { await navigator.clipboard.writeText(window.location.href); setNotice('View link copied. It opens on a device running this local portal.'); }
    catch { setNotice('Your current view is in the address bar. Copy that link to keep it.'); }
  }

  if (!ready) return <div className="workspace-boot" data-quiet-workspace="dense-tarzana-v21" role="status">Opening Quiet LA…</div>;
  if (pilot) return <><PolicyPortal /><Link className="return-dense" href="/" onClick={() => setPilot(false)}>Back to the dense Tarzana map</Link></>;
  const value = selected ? valueFor(selected, scenario, period) : null;
  const validValue = value !== null && Number.isFinite(value) && value !== -99;
  return <main className={`workspace ${expanded ? 'is-expanded' : ''}`}>
    <header className="workspace-header">
      <Link href="/" className="workspace-brand" aria-label="Quiet LA home">Quiet LA<span>Noise explorer</span></Link>
      <div className="workspace-scope">Tarzana <span>· Internal study</span></div>
      <Choice label="Map dimension" value={mode3d ? '3d' : '2d'} options={[['2d', '2D'], ['3d', '3D']]} onChange={(mode) => setMode3d(mode === '3d')} />
    </header>
    <DenseMap period={period} scenario={scenario} treatment={treatment} mode3d={mode3d} sourcesVisible={sourcesVisible} contextVisibility={contextVisibility} selected={selected} fitRequest={fitRequest} fitContextRequest={fitContextRequest} target={target} onSelect={inspect} onContextSelect={inspectContext} onStatus={setStatus} onCamera={writeView} onModeFallback={fallback} />
    <aside className="map-guide" aria-label="Map controls and inspection">
      <div className="guide-heading"><h1>Explore Tarzana</h1><button type="button" className="sheet-toggle" aria-expanded={expanded} aria-controls="guide-body" onClick={() => setExpanded(!expanded)}>{expanded ? 'Less' : 'Controls'}<Icon name="chevron" /></button></div>
      <p className="guide-intro">Surface-road study · {scenario}-source assumed alternative. Freeway, aircraft and siren noise are not included in this surface.</p>
      <div className="quick-controls"><Choice label="Scenario period" value={period} options={[['D', 'Day'], ['E', 'Evening'], ['N', 'Night']]} onChange={setPeriod} /></div>
      <div id="guide-body" className="guide-body">
        <form className="place-search" onSubmit={search}><label htmlFor="place-search">Go to a place</label><div><input id="place-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tarzana or latitude, longitude" autoComplete="off" /><button type="submit" aria-label="Find place"><Icon name="search" /></button></div><p role="status">{searchMessage || 'Search the study area or paste map coordinates.'}</p></form>
        <section className="guide-section" aria-labelledby="appearance-heading"><h2 id="appearance-heading">Display</h2><Choice label="Noise display style" value={treatment} options={[['field', 'Field'], ['bands', 'Bands'], ['dots', 'Dots'], ['glow', 'Glow']]} onChange={setTreatment} /><p className="control-help">{treatment === 'dots' ? 'Exact receiver samples. Zoom in for every nearby receiver.' : 'A fixed visual surface. Select the map for an exact receiver value.'}</p></section>
        <details className="advanced-controls"><summary>Advanced / research <span className="advanced-current">{scenario}-source alternative</span><Icon name="chevron" /></summary><section className="guide-section" aria-labelledby="scenario-heading"><h2 id="scenario-heading">Surface-road input set</h2><Choice label="Traffic scenario" value={scenario} options={[['23', '23 sources'], ['585', '585 sources']]} onChange={setScenario} /><p className="control-help">23 count-linked segments or a broader 585-segment historical scenario. These alternatives cannot be summed and neither includes freeway, aircraft, or siren contributions.</p><label className="source-toggle"><input type="checkbox" checked={sourcesVisible} onChange={(event) => setSourcesVisible(event.target.checked)} />Show modeled road sources</label></section><section className="guide-section" aria-labelledby="context-heading"><h2 id="context-heading">Context layers</h2><p className="control-help">Optional location references. They are inspected separately and never change the numeric surface.</p><div className="context-options">{(Object.keys(CONTEXT_DEFAULTS) as ContextLayerId[]).map((id) => <label className={`context-toggle context-${id}`} key={id}><input type="checkbox" checked={contextVisibility[id]} onChange={(event) => setContextVisibility((current) => ({ ...current, [id]: event.target.checked }))} /><span>{CONTEXT_LABELS[id]}</span></label>)}</div><button type="button" className="context-fit" onClick={() => setFitContextRequest((n) => n + 1)}>Fit visible context</button></section></details>
        <div className="guide-actions"><button type="button" onClick={() => { setFitRequest((n) => n + 1); setExpanded(false); }}>Fit study area</button><button type="button" onClick={copyView}><Icon name="share" />Copy view</button></div>
      </div>
      <section ref={receiverRef} className="receiver-section" aria-label="Receiver inspection" aria-live="polite">
        {selected ? <><div className="receiver-heading"><span>{selected[3] === 1 ? 'Exterior façade receiver' : 'Open-space receiver'}</span><button type="button" className="plain-icon" aria-label="Close inspection" onClick={() => { setSelected(null); setHasInspected(false); }}><Icon name="close" /></button></div><div className="receiver-result"><strong>{validValue ? value!.toFixed(1) : 'No result'}</strong><span>{validValue ? 'relative model index' : 'Not evidence of quiet'}</span></div><p className="receiver-meta">Receiver {selected[0]} · {Math.round(distance)} m from selection</p><div className="inspect-details"><p>{periodName[period]} · {scenario}-source alternative. This value belongs to the receiver, not a home or building interior.</p><button type="button" className="save-button" onClick={saveReceiver}><Icon name="bookmark" />{saved.some((item) => item.id === selected[0]) ? 'Saved receiver' : 'Save receiver'}</button></div></> : contextSelection ? <><div className="receiver-heading"><span>{contextSelection.detail}</span><button type="button" className="plain-icon" aria-label="Close inspection" onClick={() => { setContextSelection(null); setHasInspected(false); }}><Icon name="close" /></button></div><div className="context-result"><strong>{contextSelection.title}</strong><span>Context reference</span></div><div className="inspect-details"><p>{contextSelection.note}</p></div></> : <div className="inspection-empty"><strong>{hasInspected ? 'No nearby modeled receiver' : 'Select a location on the map'}</strong><p>{hasInspected ? 'This location has no receiver within the inspection range. Missing coverage does not mean quiet.' : 'See the exact result, source scenario, and distance from your selection.'}</p></div>}
      </section>
      <div className="guide-secondary">
        <details className="saved-places"><summary>Saved receivers <span>{saved.length} / 6</span></summary>{saved.length === 0 ? <p>Save receivers from the map to compare them here. They stay on this device.</p> : <><p>Same model, {scenario} sources · {periodName[period].toLowerCase()}. Receiver values only.</p><div className="comparison-list">{saved.map((item) => { const current = valueFor(item.record, scenario, period); return <div className="comparison-row" key={item.id}><button type="button" onClick={() => { setTarget({ lng: item.record[1], lat: item.record[2], nonce: Date.now() }); setSelected(item.record); setDistance(0); setHasInspected(true); setExpanded(false); }}>{item.name}<small>{item.record[3] === 1 ? 'Exterior façade' : 'Open space'}</small></button><strong>{current === -99 ? 'No result' : current.toFixed(1)}</strong><button type="button" className="plain-icon" aria-label={`Remove ${item.name}`} onClick={() => setSaved((items) => items.filter((entry) => entry.id !== item.id))}><Icon name="close" /></button></div>; })}</div></>}</details>
        <details className="study-details"><summary>Coverage & model details<Icon name="chevron" /></summary><p><strong>229,981 receivers across 30 completed surface-road tiles.</strong> This is the surface-road component, not the combined noise study. Freeway contributions and other parts of LA County are not covered by this surface. The combined-road campaign and corrected regional integration remain unfinished.</p><p>These are synthetic assumed historical-context scenarios. They are uncalibrated: not measured dBA/CNEL, not current traffic, and not predictions for an address. Reflections are not modeled.</p><p>Field, Bands and Glow are display images. In 3D, terrain and eligible buildings provide geometric context; the color surface does not represent airborne sound volume.</p><p>The invalidated legacy regional results remain withheld. The 23-source and 585-source alternatives cannot be summed.</p><Link href="/pilot">Open the separate mixed-road pilot and context layers</Link></details>
        <p className="workspace-notice" role="status">{notice}</p>
      </div>
    </aside>
    <div className="workspace-legend" aria-label="Map legend"><div><span>Lower modeled</span><span>Higher modeled</span></div><div className={`color-scale ${treatment === 'bands' ? 'is-banded' : ''}`} aria-hidden="true" /><p>Relative, uncalibrated · Blank areas are not evidence of quiet</p></div>
    {(status.error || status.loading || status.threeDLoading || !status.inCoverage) && <div className={`workspace-state ${status.error ? 'is-error' : ''}`} role={status.error ? 'alert' : 'status'}>{status.error || (status.threeDLoading ? 'Loading terrain and buildings…' : status.loading ? 'Loading modeled coverage…' : 'Outside the modeled Tarzana surface. Enabled context layers may still be available.')} {!status.loading && !status.threeDLoading && <button type="button" onClick={() => { if (status.error) window.location.reload(); else setFitRequest((n) => n + 1); }}>{status.error ? 'Retry' : 'Return to coverage'}</button>}</div>}
    <div className="workspace-attribution"><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a><span> · Quiet LA</span></div>
  </main>;
}

export default function HomePage() { return pilotOnly ? <PilotPortal /> : local ? <DenseWorkspace /> : <PolicyPortal />; }
