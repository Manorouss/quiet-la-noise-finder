"""Join inferred VNY path-fragment candidates to auditable ANP readiness.

This is a private review handoff, not a model execution or public data layer.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()


def build(inferred_path: Path, mapping_path: Path) -> dict:
    inferred=json.loads(inferred_path.read_text()); mappings=json.loads(mapping_path.read_text())
    by_code={row["icao_type_code"]:row for row in mappings["coverage"]}
    op_count=Counter(); status_count=Counter(); code_count=Counter(); rows=[]
    for candidate in inferred["candidate_rows_internal_only"]:
        code=candidate["type_code"] or "missing"; op=candidate["operation_candidate"]
        table=by_code.get(code,{}); letter="A" if op=="arrival" else "D"
        op_mapping=table.get("operation_candidates",{}).get(letter,{})
        explicit=bool(table.get("substitution_table_rows",0) and op_mapping.get("selected_ANP_proxy_internal_only"))
        if explicit:
            status="official_substitution_and_ANP_operation_data_candidate_pending_root_review"
        elif code=="P28A":
            status="manual_family_variant_substitution_candidate_PA28_model_pending_root_approval"
        elif code=="C172":
            status="manual_family_variant_substitution_candidate_CNA172_model_pending_root_approval"
        elif table.get("substitution_table_rows",0):
            status="official_substitution_row_but_incomplete_operation_data"
        else:
            status="no_qualified_ICAO_substitution_or_manual_mapping_yet"
        row={"internal_path_fragment_id":candidate["path_id_internal_only"],"icao_type_code":code,
             "operation_candidate":op,"classification":"inferred_candidate_not_confirmed_movement",
             "runway_axis_candidate_ends":candidate["candidate_runway_ends"],
             "runway_assignment":candidate["runway_assignment"],"position_source_counts":candidate["position_source_counts"],
             "source_quality_all_adsb_icao":candidate["source_quality_all_adsb_icao"],
             "acoustic_mapping_status":status,"explicit_substitution_mapping_operation":op_mapping if explicit else None,
             "candidate_coverage":"path_fragment_only; duplicate/missed movements and source gaps unresolved"}
        rows.append(row); op_count[op]+=1; status_count[(op,status)]+=1; code_count[(op,code)]+=1
    # Prescriptive exclusions and candidate rows are distinct: no proxy is
    # filled for unsupported types, and no acoustic values are calculated here.
    return {"schema":"quiet_la_vny_doc29_candidate_model_review_v1_internal",
            "source_hashes":{"inferred_candidates_sha256":sha(inferred_path),"ANP_substitution_readiness_sha256":sha(mapping_path),
                             "activity_sha256":inferred["activity_sha256"],"runway_query_sha256":inferred["runway_query_sha256"]},
            "date_local":inferred["date_local"],"utc_window":inferred["utc_window"],
            "counts":{"candidate_path_fragments_by_operation_not_movement_count":dict(op_count),
                      "candidate_path_fragments_by_operation_and_mapping_status":{op:{status:n for (o,status),n in status_count.items() if o==op} for op in sorted({o for o,_ in status_count})},
                      "candidate_path_fragments_by_type_and_operation":{op:{code:n for (o,code),n in code_count.items() if o==op} for op in sorted({o for o,_ in code_count})}},
            "decision_rules":{"official_substitution":"Use only documented per-operation maximum Δ proxy rows with SEL NPD and default ANP profile/weight from the official EASA 2018 jets/heavy-props table. Δ is applied to event NPD; equivalent movement N is never added.",
                              "P28A":"No P28A ICAO row in the 2018 jets/heavy-props workbook. EASA ANP v2.3 has PA28, specifically Piper Warrior PA-28-161/O-320-D3G. This is a possible family/variant manual substitution only; no Δ is supplied and it requires root acceptance.",
                              "C172":"No C172 ICAO row in the 2018 jets/heavy-props workbook. EASA ANP v2.3 has CNA172, specifically Cessna 172R/IO-360-L2A. This is a possible family/variant manual substitution only; no Δ is supplied and it requires root acceptance.",
                              "all_other_unmapped":"Remain excluded until a documented code/model mapping and suitable ANP NPD/profile is shown; no family-level guess."},
            "candidate_rows_internal_only":rows,
            "model_status":"No SEL calculations performed. These are source-matched candidate fragments for root review only. No activity completeness, movement counts, or measured-accuracy claim."}


def main():
    p=argparse.ArgumentParser(); p.add_argument("--inferred",type=Path,required=True); p.add_argument("--mappings",type=Path,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    result=build(a.inferred,a.mappings); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result["counts"],indent=2))


if __name__=="__main__":main()
