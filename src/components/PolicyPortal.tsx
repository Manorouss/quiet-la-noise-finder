'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react';
import MapCanvas, { type InspectionRecord, type MapPayload } from '@/components/MapCanvas';
import {
  assertContract,
  classLabel,
  contract,
  layerById,
  layerVisibleInView,
  layers,
  type LayerStyle,
  type NoiseView,
  type Period,
} from '@/lib/contracts';
import { initialLayerToggles, resolveRuntimeProfile, scientificAssetUrl } from '@/lib/runtime-profile.js';
import { inspectionForView, layerDisplayAllowed, WITHHELD_LAYERS } from '@/lib/map-state.js';

type LoadState = 'loading' | 'ready' | 'error';

const runtimeProfile = resolveRuntimeProfile(process.env.NEXT_PUBLIC_QUIET_LA_DATA_PROFILE);
const localProfile = runtimeProfile.mode === 'local';
const privatePreview = runtimeProfile.mode === 'private_preview';
const payloadFreeExternal = runtimeProfile.mode === 'external_payload_free';
const payloadUnavailable = !runtimeProfile.scientificPayloadsAvailable;
const unavailableLayers = new Set(layers.map((layer) => layer.id));
const localRecoveryCommand = process.env.NEXT_PUBLIC_QUIET_LA_LOCAL_RECOVERY_COMMAND ?? '';

const emptyPayload: MapPayload = {
  four: { records: [] },
  tarzana: { records: [] },
  airports: { features: [] },
  rail: { metro_routes: [], overall_status: 'not_admitted_for_this_runtime_profile' },
  mask: null,
};

async function fetchJson(path: string): Promise<unknown> {
  const response = await fetch(scientificAssetUrl(runtimeProfile, path), { cache: 'no-store' });
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  if (!path.endsWith('.gz')) return response.json();
  if (typeof DecompressionStream === 'undefined') throw new Error('This browser cannot decode the local gzip payload');
  const stream = new Blob([await response.arrayBuffer()]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).json();
}

async function loadPayloads(): Promise<MapPayload> {
  if (!localProfile) return emptyPayload;
  const [tarzana, airports, rail, mask] = await Promise.all([
    fetchJson('tarzana-scenario.json'),
    fetchJson('context/airport-contours.json.gz'),
    fetchJson('context/rail.json'),
    fetchJson('context/source341-mask.json'),
  ]);
  return { four: { records: [] }, tarzana, airports, rail, mask } as MapPayload;
}

function radioKeys(event: KeyboardEvent<HTMLDivElement>) {
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
  const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]:not(:disabled)')];
  if (!buttons.length) return;
  const index = buttons.indexOf(event.target as HTMLButtonElement);
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
    : (index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + buttons.length) % buttons.length;
  event.preventDefault();
  buttons[next].focus();
  buttons[next].click();
}

const regionPresets = [
  { id: 'la', label: 'LA overview' },
  { id: 'tarzana', label: 'Tarzana' },
  { id: 'east-valley', label: 'East Valley' },
  { id: 'central-ne', label: 'Central / Northeast' },
  { id: 'hollywood-westside', label: 'Hollywood / Westside' },
] as const;

export default function HomePage() {
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [payload, setPayload] = useState<MapPayload | null>(null);
  const [error, setError] = useState('');
  const [view, setView] = useState<NoiseView>('all');
  const [period, setPeriod] = useState<Period>('D');
  const [layerStyle, setLayerStyle] = useState<LayerStyle>('field');
  const [inspection, setInspection] = useState<InspectionRecord[] | null>(null);
  const [limitsOpen, setLimitsOpen] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const limitsTriggerRef = useRef<HTMLButtonElement | null>(null);
  function openLimits(event: MouseEvent<HTMLButtonElement>) {
    limitsTriggerRef.current = event.currentTarget;
    setLimitsOpen(true);
  }
  function finishClosingLimits() {
    setLimitsOpen(false);
    limitsTriggerRef.current?.focus();
  }
  const [layerToggles, setLayerToggles] = useState<Record<string, boolean>>(
    initialLayerToggles(runtimeProfile, layers.map((layer) => layer.id)),
  );
  const [fitRequest, setFitRequest] = useState(0);
  const [fitVisibleRequest, setFitVisibleRequest] = useState(0);
  const [regionRequest, setRegionRequest] = useState(localProfile ? 'tarzana' : 'la');
  const inspect = useCallback((records: InspectionRecord[]) => setInspection(records), []);
  const tarzanaById = useMemo(() => new Map(payload?.tarzana.records.map((record) => [`tarzana-${record.receiver_id}`, record]) ?? []), [payload]);
  const selected: InspectionRecord[] = useMemo(() => inspectionForView(inspection ?? [], tarzanaById, period, view, layerToggles), [inspection, tarzanaById, period, view, layerToggles]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (limitsOpen && !dialog.open) dialog.showModal();
    else if (!limitsOpen && dialog.open) dialog.close();
  }, [limitsOpen]);

  const load = useCallback(async () => {
    setLoadState('loading');
    setError('');
    try {
      assertContract();
      setPayload(await loadPayloads());
      setLoadState('ready');
    } catch (cause) {
      setLoadState('error');
      setError(cause instanceof Error ? cause.message : 'The accepted v3 display payload could not be loaded.');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const mappedLayers = useMemo(() => layers.filter((layer) => layer.id !== 'metro_rail_context'), []);
  const railLayer = layerById.get('metro_rail_context');
  const activeLayers = useMemo(() => mappedLayers.filter((layer) => layerToggles[layer.id] && layerVisibleInView(layer, view)), [mappedLayers, layerToggles, view]);
  const showRailContext = view !== 'modeled';
  const railContextAvailable = showRailContext && localProfile;
  const modeledVisible = activeLayers.some((layer) => layer.family === 'freeway' || layer.family === 'tarzana_scenario');
  const contextVisible = activeLayers.some((layer) => layer.family === 'aviation_context' || layer.family === 'source_341');
  const receiverCount = payload
    ? (activeLayers.some((layer) => layer.id === 'four_region_freeway_relative') ? payload.four.records.length : 0)
      + (activeLayers.some((layer) => layer.id === 'tarzana_mixed_road_scenario') ? payload.tarzana.records.length : 0)
    : 0;
  const detailCount = selected.length;
  const mappedFamilyCount = activeLayers.length;

  function toggleLayer(id: string) {
    if (!layerDisplayAllowed(id)) return;
    setLayerToggles((current) => ({ ...current, [id]: !current[id] }));
  }

  function chooseView(next: NoiseView) {
    setView(next);
  }

  return (
    <main className={`app-shell ${inspection !== null ? 'inspect-open' : ''}`} aria-labelledby="product-name">
      <MapCanvas
        payload={payload}
        loadState={loadState}
        view={view}
        period={period}
        layerStyle={layerStyle}
        layerToggles={layerToggles}
        fitRequest={fitRequest}
        fitVisibleRequest={fitVisibleRequest}
        regionRequest={regionRequest}
        onInspect={inspect}
        selectedIds={selected.map((record) => record.id)}
        onFitComplete={() => setFitRequest(0)}
        runtimeMode={runtimeProfile.mode}
      />

      <header className="brand-instrument glass" aria-label="Quiet LA identity">
        <div className="brand-lockup"><span className="brand-mark" aria-hidden="true">QL</span><div><h1 id="product-name">Quiet LA</h1><p>Noise portal</p></div></div>
        <span className="internal-badge">{runtimeProfile.badge}</span>
        <button className="icon-button" type="button" onClick={openLimits} aria-haspopup="dialog" aria-controls="limits-dialog" aria-label="Open model limits">i</button>
      </header>

      <section className="noise-view-instrument glass" aria-label="Noise view">
        <div className="instrument-heading"><strong>Explore the map</strong></div>
        <div className="segmented-control view-options" role="radiogroup" onKeyDown={radioKeys} aria-label="Choose noise view">
          {([['all', 'ALL DATA'], ['modeled', 'MODELED'], ['context', 'CONTEXT']] as const).map(([id, label]) => (
            <button key={id} className={`segment ${view === id ? 'is-active' : ''}`} type="button" role="radio" tabIndex={view === id ? 0 : -1} aria-checked={view === id} onClick={() => chooseView(id)}>{label}</button>
          ))}
        </div>
        <p className="view-summary">{payloadFreeExternal ? 'Scientific private preview is not published on this URL' : privatePreview ? 'Scientific and context payloads are unavailable pending rights admission' : view === 'all' ? 'Visual co-display only · never an acoustic sum' : view === 'modeled' ? 'Modeled layers only · each result remains separate' : 'Official context and incomplete overlays only'}</p>
        <p className="headline-boundary">{payloadFreeExternal ? 'Payload-free external shell. No scientific or context layer is active.' : privatePreview ? 'Protected preview shell only. No scientific or context payload is admitted.' : contract.headlineBoundary}</p>
        <div className="preset-row">
          <label htmlFor="region-preset">Region</label>
          <select id="region-preset" value={regionRequest} onChange={(event) => { setRegionRequest(event.target.value); setFitRequest((value) => value + 1); }}>
            {regionPresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}
          </select>
          <button className="text-button" type="button" onClick={() => setFitVisibleRequest((value) => value + 1)}>Fit visible</button>
        </div>
      </section>

      <section className="period-instrument glass" aria-label="Scenario period">
        <span className="control-label">Scenario period</span>
        <div className="period-options" role="radiogroup" onKeyDown={radioKeys} aria-label="Choose scenario period">
          {(['D', 'E', 'N'] as const).map((value) => (
            <button key={value} disabled={payloadUnavailable} className={`period ${!payloadUnavailable && period === value ? 'is-active' : ''}`} type="button" role="radio" tabIndex={period === value ? 0 : -1} aria-checked={!payloadUnavailable && period === value} onClick={() => setPeriod(value)}><b aria-hidden="true">{value}</b><small>{value === 'D' ? 'Day' : value === 'E' ? 'Evening' : 'Night'}</small></button>
          ))}
        </div>
        <p className="period-note">{payloadFreeExternal ? 'Unavailable on this payload-free URL.' : privatePreview ? 'Unavailable · Tarzana scenario output rights are not externally admitted.' : 'Tarzana scenario only. Legacy regional results are withheld after an elevation error.'}</p>
      </section>

      <details className="layer-instrument glass">
        <summary><span className="layer-instrument__icon" aria-hidden="true">≋</span><span><strong>Noise layers</strong><small>{mappedFamilyCount} mapped source families · {railContextAvailable ? '1 non-spatial context record' : payloadUnavailable && showRailContext ? 'context unavailable' : 'context hidden'}</small></span></summary>
        <div className="layer-instrument__panel">
          <span className="layer-control-label">Display treatment</span>
          <div className="layer-style-options" role="radiogroup" onKeyDown={radioKeys} aria-label="Noise display style">
            {(['field', 'bands', 'dots', 'glow'] as const).map((style) => <button key={style} disabled={payloadUnavailable} className={layerStyle === style ? 'is-active' : ''} type="button" role="radio" tabIndex={layerStyle === style ? 0 : -1} aria-checked={layerStyle === style} onClick={() => setLayerStyle(style)}><span className={`style-swatch style-swatch--${style}`} aria-hidden="true" />{style[0].toUpperCase() + style.slice(1)}</button>)}
          </div>
          <span className="layer-control-label">Source/status</span>
          <div className="layer-rows">
            {mappedLayers.map((layer) => (
              <label key={layer.id} className={`layer-row ${layerVisibleInView(layer, view) ? '' : 'is-filtered'} ${(payloadUnavailable && unavailableLayers.has(layer.id)) || !layerDisplayAllowed(layer.id) ? 'layer-row--disabled' : ''}`}>
                <input type="checkbox" disabled={(payloadUnavailable && unavailableLayers.has(layer.id)) || !layerDisplayAllowed(layer.id)} checked={layerToggles[layer.id] ?? false} onChange={() => toggleLayer(layer.id)} aria-label={`Toggle ${layer.label}`} />
                <span className="layer-swatch" aria-hidden="true" data-family={layer.family} />
                <span className="layer-row-copy"><strong>{layer.label}</strong><small>{layerDisplayAllowed(layer.id) ? classLabel(layer.evidenceClass) : 'Withheld — invalidated legacy results'}</small>{localProfile && !layerDisplayAllowed(layer.id) && <em>{WITHHELD_LAYERS.four_region_freeway_relative}</em>}{payloadFreeExternal && <em>Unavailable on this payload-free URL.</em>}{privatePreview && (layer.id === 'four_region_freeway_relative' || layer.id === 'tarzana_mixed_road_scenario') && <em>Not uploaded · source/output rights are not externally admitted.</em>}{privatePreview && layer.id === 'airport_planning_contours' && <em>Not uploaded · official-record redistribution terms unresolved.</em>}{privatePreview && layer.id === 'source_341_incomplete_mask' && <em>Not uploaded · private review evidence excluded. Source-341 remains incomplete — not computable.</em>}</span>
              </label>
            ))}
            {showRailContext && railLayer && <div className={`layer-row layer-row--context ${payloadUnavailable ? 'layer-row--disabled' : ''}`} data-testid="rail-context-row" aria-label="Metro rail metadata — non-spatial context"><span className="layer-swatch" aria-hidden="true" data-family="rail_context" /><span className="layer-row-copy"><strong>Metro rail metadata — non-spatial context</strong><small>{classLabel(railLayer.evidenceClass)}</small><em>{payloadFreeExternal ? 'Unavailable on this payload-free URL.' : privatePreview ? 'Not uploaded · Metro provider terms unresolved.' : '6 active route metadata records; geometry and rail acoustics are not admitted.'}</em></span></div>}
            <div className="layer-row layer-row--disabled" aria-disabled="true"><input type="checkbox" disabled /><span className="layer-swatch" aria-hidden="true" data-family="disabled" /><span className="layer-row-copy"><strong>Compatible acoustic sum — not admitted</strong><small>Combination contract is closed</small></span></div>
          </div>
          <p className="layer-note">Filters change visibility only. Context layers are never converted to sound levels or combined with modeled results.</p>
        </div>
      </details>

      <section className={`legend-instrument glass ${contextVisible && !modeledVisible ? 'is-context' : ''}`} aria-label="Map legend">
        {payloadFreeExternal ? <><div className="legend-heading"><span>Scientific private preview not published</span><span className="legend-chip">payload-free</span></div><p>No modeled or context layer is active on this URL.</p></> : privatePreview ? <><div className="legend-heading"><span>Preview payloads unavailable</span><span className="legend-chip">not uploaded</span></div><p>Modeled outputs await source/output-rights admission. Official and Metro terms remain unresolved. Private Source-341 review evidence is excluded; Source-341 remains incomplete — not computable.</p></> : modeledVisible ? <><div className="legend-heading"><span>Modeled relative field</span><span className="legend-chip">{layerStyle}</span></div><div className="legend-ramp" aria-hidden="true" /><div className="legend-scale"><span>lower modeled</span><span>fixed display scale</span><span>higher modeled</span></div><p>Relative and uncalibrated. Colors are display treatments; not measured dBA/CNEL.</p></> : <><div className="legend-heading"><span>Context overlays</span><span className="legend-chip">no numeric scale</span></div><div className="context-key"><span className="context-key__line context-key__line--airport" />Official planning contour</div><div className="context-key"><span className="context-key__line context-key__line--mask" />Incomplete — not computable reach</div>{showRailContext && <div className="context-key"><span className="context-key__line context-key__line--rail" />Metro metadata · 6 records · context only</div>}<p>Context records and the mask are not evidence of quiet or low exposure.</p></>}
      </section>

      {inspection !== null && <aside className="inspect-card glass" aria-labelledby="inspect-title">
        <div className="card-heading"><div><h2 id="inspect-title">Map inspection</h2></div><button className="icon-button" type="button" onClick={() => setInspection(null)} aria-label="Close inspection">×</button></div>
        <p className="inspect-boundary">Nearest visible receiver symbol per source family at the selected location. Results belong to the receiver, not the clicked property.</p>
        <div className="inspection-stack" aria-live="polite">{selected.length === 0 && <p>No visible record at this location. Select a receiver symbol or enable an available layer. Missing coverage is not evidence of low exposure.</p>}{selected.slice(0, 8).map((record, index) => <article className="inspection-record" key={`${record.id}-${index}`}><div className="record-top"><strong>{record.label}</strong><span className="status-chip">{record.statusChip}</span></div><dl><div><dt>Source family</dt><dd>{record.family}</dd></div><div><dt>Region / period</dt><dd>{record.region} · {record.period ?? 'common scale'}</dd></div><div><dt>Result</dt><dd>{record.value === null ? (record.evidenceClass === 'official_record_context_only' ? 'Context only — no modeled value' : 'not computable') : `${record.value.toFixed(1)} relative index`}</dd></div>{record.distanceM !== undefined && <div><dt>Distance to receiver</dt><dd>{Math.round(record.distanceM)} m</dd></div>}<div><dt>Evidence class</dt><dd>{record.evidenceClass}</dd></div><div><dt>Current / calibration</dt><dd>{record.currentness} · {record.calibration}</dd></div><div><dt>Combination</dt><dd>Not eligible</dd></div></dl><p>{record.claimBoundary}</p></article>)}</div>
      </aside>}

      <section className="disclosure-rail glass" aria-label="Model disclosures">
        <div className="active-run"><span className="run-dot" aria-hidden="true" /><div><span className="run-label">{payloadFreeExternal ? 'Payload-free external shell' : `Active ${privatePreview ? 'private preview' : 'local'} layers`}</span><strong>{mappedFamilyCount} mapped source families + {showRailContext && localProfile ? 1 : 0} non-spatial context record · {receiverCount.toLocaleString()} receiver records</strong></div></div>
        <p><strong>{detailCount}</strong> nearest record{detailCount === 1 ? '' : 's'} per source family shown separately.</p>
        <ul><li>{payloadFreeExternal ? 'Scientific private preview is not published on this URL.' : privatePreview ? 'Modeled derivatives are not uploaded until source/output rights are externally admitted.' : 'Modeled relative / uncalibrated and scenario assumed inputs.'}</li><li>{payloadFreeExternal ? 'No official/context or private evidence payload is active.' : privatePreview ? 'Official/Metro payloads and private Source-341 review evidence are not uploaded; Source-341 remains incomplete — not computable.' : 'Official records are context only; the mask is incomplete — not computable.'}</li><li>Not current traffic; not live traffic; not observed traffic; not measured dBA/CNEL; not quietness; not an address prediction.</li><li>Compatible acoustic sum is disabled until its exact contract is admitted.</li></ul>
        <button className="mobile-limits-button" type="button" onClick={openLimits}>Model limits · open disclosures</button>
      </section>
      <p className="map-attribution"><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a> · {payloadFreeExternal ? 'Quiet LA payload-free shell' : privatePreview ? 'Quiet LA private preview shell' : 'Quiet LA modeled display'}</p>

      {loadState === 'loading' && <div className="load-state glass" role="status" aria-live="polite"><span className="spinner" aria-hidden="true" />{payloadFreeExternal ? 'Loading the payload-free map shell…' : privatePreview ? 'Loading the private preview shell…' : 'Loading the accepted v3 display layers…'}</div>}
      {loadState === 'error' && localProfile && <div className="load-state load-state--error glass" role="alert"><strong>Accepted display data is not staged.</strong><span>{error}</span><button className="load-state__action" type="button" onClick={() => void load()}>Retry</button>{runtimeProfile.developerRecoveryAllowed && localRecoveryCommand && <small>Run <code>{localRecoveryCommand}</code> from this app, then retry.</small>}</div>}

      <dialog ref={dialogRef} className="method-dialog" id="limits-dialog" onClose={finishClosingLimits} onCancel={() => setLimitsOpen(false)} aria-labelledby="limits-title">
        <div className="dialog-accent" aria-hidden="true" /><div className="card-heading"><div><h2 id="limits-title">Model limits</h2></div><button className="icon-button" type="button" onClick={() => setLimitsOpen(false)} aria-label="Close model limits">×</button></div>
        <p><strong>Explore the available evidence.</strong> ALL DATA is a visual co-display of separate source families. It never creates an acoustic sum.</p>
        <p><strong>Regional results withheld.</strong> The legacy four-region display contains an elevation error. Corrected runs require a new reviewed data binding before display.</p><p><strong>Exact layers.</strong> {payloadFreeExternal ? 'No scientific, official-context, or private-evidence payload is published on this URL.' : privatePreview ? 'No scientific or context payload is uploaded. Four-region and Tarzana derivatives await source/output-rights admission. Airport and Metro terms remain unresolved. Private Source-341 review evidence is excluded.' : 'The freeway field is modeled relative and uncalibrated. Tarzana is an assumed-input scenario. Airport and Metro records are official context only.'} Source-341 is explicitly incomplete — not computable.</p>
        <p><strong>Periods.</strong> D/E/N changes only the Tarzana scenario, which advertises those periods. The four-region layer keeps its common scale and does not invent temporal values.</p>
        <p><strong>Hard limits.</strong> Not measured dBA/CNEL; not current traffic; not live traffic; not observed traffic; not quietness; not an address prediction. Building interiors, calibration, and compatible cross-family combination are not admitted.</p>
        <button className="primary-button" type="button" onClick={() => setLimitsOpen(false)}>Return to the map</button>
      </dialog>
    </main>
  );
}
