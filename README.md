# Quiet LA web foundation

This is the local Next.js App Router + TypeScript + MapLibre foundation for the one-user-facing Noise experience. The recovered dense workspace and retained policy portal are documented in [`DESIGN_PARITY.md`](DESIGN_PARITY.md).

## Local preview

```sh
npm run stage:local
npm run dev
```

The default local workspace restores the v2.1 dense Tarzana study: **229,981 exact receivers**, alternative **23-source and 585-source** synthetic traffic scenarios, Day/Evening/Night, real Field/Bands/Glow rasters, receiver Dots, and terrain/building 3D. A single sidebar becomes a compact bottom sheet on mobile. Surface clicks inspect the nearest exact receiver within 30 m; saved comparisons follow the selected period and scenario. Search accepts coordinates and the study's named coverage areas; street-address geocoding is not implemented.

`stage:local` validates and copies manifest-bound dense assets (99 files, 22,400,832 source bytes) into ignored `public/_local-data/dense/`, plus the existing v3 package. It rejects missing, tampered, or symlinked assets. Existing data is reused; no simulation engine run is performed. The earlier mixed-road pilot and admitted context remain accessible at `/?study=pilot`. The invalidated legacy four-region payload stays withheld from fetching, rendering, fitting, and inspection.

These are internal synthetic alternatives, never an additive acoustic sum. The footprint is Tarzana, not all of LA County. County coverage requires additional admitted inputs, partitioned simulation runs, validation, and tiled delivery; a wider basemap does not supply new results.

The local launcher and profile builds automatically stage the MapLibre worker and shared module from the installed package into ignored `public/maplibre/`. Export checks require both files to match that installed version exactly.

## Hosted Tarzana pilot

The production Vercel profile is the deterministic 80-building Tarzana combined-road pilot. `src/data/pilot-release-v1/` is the portable, hash-bound release bundle: the reviewed receiver and building files remain byte-identical, while `build-manifest.json` removes local source paths and retains counts, masks, and source hashes. The private delivery receipt binds that projection to original source-manifest SHA256 `f2f47b8a1a6a2af1c7885c763b12e58b970bb563a70822f6e1cbe7cd2e2fac37`. `npm run build:hosted-pilot` builds into `out/` and admits only those three pilot assets, the MapLibre worker/runtime, and static app files. The export verifier records each file hash in `release/HOSTED_PILOT_EXPORT_MANIFEST.json`; it rejects dense/context layers, preview payloads, unexpected files, and credential-like content. Run `npm run package:vercel-pilot` after the build to assemble `.vercel/output` for `vercel deploy --prebuilt`; the upload receipt beside the portal report hashes every uploaded file. `npm run build:pilot` makes the same verified package under the delivery evidence directory for review. Both `/` and `/pilot/` open the building pilot.

The pilot disclosure names LA County DPW StreetMap Primary/Secondary as local-road geometry, Caltrans as freeway/traffic context, and OpenStreetMap as basemap. It identifies the synthetic historical-context traffic scenario, evening/night factors, 113 road segments without assigned activity, unavailable receivers, and the uncalibrated exterior LAeq scope. This pilot does not admit the larger dense/context payloads, private preview evidence, or other neighborhood models. Production and ordinary preview builds use the pilot profile; the `private-preview` branch retains its separate payload-free preview build. Git-triggered Vercel deployments remain disabled; publication uses the reviewed candidate with an explicit Vercel CLI deployment.

## Validation

```sh
npm run typecheck
npm run lint
npm run test:contracts
npm run verify:replacement-adapter
npm run test:browser
npm run build:local
npm run build:private-preview
npm run test:browser:private-preview
```

`npm run build` deliberately refuses an unprofiled external build. `npm run build:private-preview` stages and verifies the exact private payload, builds the static export, and audits that export. `npm run manifest:clean` writes the deterministic code/tests/schema-only manifest under `release/`.

The useful preview is explicitly local-only until terms, authentication, and external platform admission are separately recorded. It does not claim measured/current/live noise, dBA/CNEL, quietness, or address prediction. Airport and Metro records are context only; Source-341 is incomplete — not computable.

Stop the development server before running profile builds or a second profile server: Next static export still shares framework build state during compilation. Restart `npm run dev` after the builds.

`npm run test:browser` runs the dense recovery interaction suite in both Chromium and WebKit. The WebKit result is WebKit coverage only; it is not a claim of testing a user's Safari installation. `src/data/replacement-layer-admission-manifest.json` is intentionally empty and not admitted; `verify:replacement-adapter` reports structural validity but always remains blocked until an independent admission audit and master release audit exist. Same-manifest rights flags, hashes, URLs, or status text can never authorize a future preview.
