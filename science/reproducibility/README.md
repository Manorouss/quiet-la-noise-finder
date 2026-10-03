# Scientific source and reproduction bundle

This directory preserves a source-only snapshot of selected acoustic methods and their focused regression tests. It does not contain aircraft observations, ANP tables, raw workbooks, receiver inventories, campaign runs, generated sound maps, installation binaries, or user credentials. The repository's application build does not import this folder.

The manifest binds each copied file to its source path, byte length, and SHA-256, and lists upstream reference inputs by URL and hash. Reference input data stay in the project evidence store under their existing access and license terms; the manifest does not grant permission to redistribute them. `AUDIT_SUMMARIES.json` records the limited review scopes and does not accept full-map accuracy or measured exposure.

Install the optional workbook reader if it is not already available (`python3 -m pip install -r science/reproducibility/requirements-source-only.txt`). Run the source-only checks from the app repository root:

```sh
python3 science/reproducibility/run_source_only_checks.py
PYTHONPATH=science/reproducibility python3 -m unittest discover -s science/reproducibility/implementation/models/siren_events/tests -v
python3 -m unittest discover -s science/reproducibility/noisemodelling-dp0/tests -v
python3 science/reproducibility/verify_manifest.py
```

The aircraft test runner intentionally selects only data-free arithmetic and geometry checks. The full upstream test collection requires external evidence inputs that are excluded here. The experimental VNY fixed-profile event adapter is source-only and is not a reviewed release model; it requires separately controlled event windows and official reference inputs to run. Its generated scenario outputs are excluded.

The NoiseModelling patch is a narrow proposed GPL-3.0-or-later adaptation for exact-zero ground-path limits in the homogeneous and favourable branches. It is not an engine build. The accompanying Java harness and synthetic H/F regression vectors can be replayed against separately compiled NoiseModelling v6.0.0 classes; no engine JAR, compiled class, campaign ray data, or private receiver identifier is included. The root review accepted one bounded diagnostic and one receiver check only, not full-tile acceptance or field validation.

The siren modules implement received-event energy accounting and conditional moving-source transfer. They do not estimate local siren power, routes, activation, event frequency, or county-wide exposure. The conditional NBS runner needs the externally sourced digitization input listed in the manifest; the input itself is not included.

See `NOTICE.md`, `noisemodelling-dp0/BUILD_AND_REPLAY.md`, and `MANIFEST.json` for provenance, scope, license notes, and exact file identities.
