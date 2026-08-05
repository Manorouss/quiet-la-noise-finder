import rawContract from '@/data/layer-contract.json';
import { claimTextIsSafe as sharedClaimTextIsSafe } from './claim-policy.js';

export type Period = 'D' | 'E' | 'N';
export type NoiseView = 'all' | 'modeled' | 'context';
export type LayerStyle = 'field' | 'bands' | 'dots' | 'glow';
export type EvidenceClass =
  | 'measured'
  | 'modeled_calibrated'
  | 'modeled_relative_uncalibrated'
  | 'scenario_assumed_inputs'
  | 'official_record_context_only'
  | 'incomplete_not_computable'
  | 'not_shown';

export type LayerFamily =
  | 'freeway'
  | 'tarzana_scenario'
  | 'aviation_context'
  | 'rail_context'
  | 'source_341';

export interface TaxonomyClass {
  id: EvidenceClass;
  label: string;
  displayAllowed: boolean;
}

export interface LayerContract {
  id: string;
  label: string;
  family: LayerFamily;
  asset: string;
  sourceHash: string;
  sourceBytes: number;
  evidenceClass: EvidenceClass;
  statusChip: string;
  metricKind: string;
  periods: Period[];
  calibration: string;
  currentness: string;
  acousticCombinationEligible: false;
  externalDisposition: string;
  claimBoundary: string;
  attribution: string;
}

export interface LayerContractDocument {
  schema: string;
  product: string;
  headlineBoundary: string;
  coDisplayRule: 'visual_only_never_acoustic_combination';
  externalDeploymentAuthorized: false;
  compatibleCombination: {
    id: string;
    label: string;
    admitted: false;
    reason: string;
  };
  taxonomy: TaxonomyClass[];
  layers: LayerContract[];
  forbiddenPositiveFragments: string[];
}

export const contract = rawContract as LayerContractDocument;
export const taxonomy = new Map(contract.taxonomy.map((entry) => [entry.id, entry]));
export const layers = contract.layers;
export const layerById = new Map(layers.map((layer) => [layer.id, layer]));

export function classLabel(value: EvidenceClass): string {
  return taxonomy.get(value)?.label ?? value;
}

export function layerVisibleInView(layer: LayerContract, view: NoiseView): boolean {
  if (view === 'modeled') return layer.family === 'freeway' || layer.family === 'tarzana_scenario';
  if (view === 'context') return layer.family === 'aviation_context' || layer.family === 'rail_context' || layer.family === 'source_341';
  return true;
}

export function supportsPeriod(layer: LayerContract, period: Period): boolean {
  return layer.periods.includes(period);
}

export function claimTextIsSafe(text: string): boolean {
  return sharedClaimTextIsSafe(text, contract.forbiddenPositiveFragments);
}

export function assertContract(doc: LayerContractDocument = contract): void {
  if (doc.schema !== 'quiet_la_web_layer_contract_v1') throw new Error('unexpected layer contract schema');
  if (doc.coDisplayRule !== 'visual_only_never_acoustic_combination') throw new Error('co-display rule widened');
  if (doc.externalDeploymentAuthorized !== false) throw new Error('external deployment must remain disabled');
  if (doc.compatibleCombination.admitted !== false) throw new Error('compatible combination must remain disabled');
  const expectedClasses: EvidenceClass[] = [
    'measured', 'modeled_calibrated', 'modeled_relative_uncalibrated',
    'scenario_assumed_inputs', 'official_record_context_only',
    'incomplete_not_computable', 'not_shown',
  ];
  if (doc.taxonomy.length !== expectedClasses.length || expectedClasses.some((id, index) => doc.taxonomy[index]?.id !== id)) {
    throw new Error('taxonomy classes must be complete and ordered');
  }
  for (const layer of doc.layers) {
    if (layer.acousticCombinationEligible !== false) throw new Error(`${layer.id} cannot be acoustically combined`);
    if (!claimTextIsSafe(layer.claimBoundary)) throw new Error(`${layer.id} contains a forbidden positive claim`);
    if (!claimTextIsSafe(layer.attribution)) throw new Error(`${layer.id} attribution contains a forbidden claim`);
    if (!taxonomy.has(layer.evidenceClass)) throw new Error(`${layer.id} has an unknown evidence class`);
  }
}

assertContract();
