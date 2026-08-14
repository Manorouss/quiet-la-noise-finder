# Quiet LA web foundation

This is the local Next.js App Router + TypeScript + MapLibre foundation for the one-user-facing Noise experience. It follows the deployed Quiet LA portal shell documented in [`DESIGN_PARITY.md`](DESIGN_PARITY.md).

## Local preview

```sh
npm run stage:local
npm run dev
```

The local stage copies only the accepted v3 display payloads into `public/_local-data/`. It is ignored and hash-bound. No symlinks are accepted. The source families remain separate; `ALL DATA` is visual co-display only and the compatible acoustic sum is disabled.

## Private Vercel preparation

The private-preview package currently contains **zero scientific payload bytes**. Existing external-readiness records do not admit source/output rights for the four-region or Tarzana derivatives, so every scientific/context row is unavailable. Airport and Metro payloads stay excluded pending terms. Private Source-341 review evidence also remains excluded and the UI retains the incomplete—not-computable warning. See [`PRIVATE_PREVIEW_DEPLOYMENT.md`](PRIVATE_PREVIEW_DEPLOYMENT.md) for the fail-closed Vercel Authentication and rights gate.

No empty-shell deployment is recommended. Any future useful payload is branch-confined: only `private-preview` may build it after an exact-hash rights admission. `main` and production payload builds are machine-refused; production remains payload-free.

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

`npm run test:browser` runs the local-v3 interaction suite in both Chromium and WebKit. The WebKit result is WebKit coverage only; it is not a claim of testing a user's Safari installation. `src/data/replacement-layer-admission-manifest.json` is intentionally empty and not admitted; `verify:replacement-adapter` reports structural validity but always remains blocked until an independent admission audit and master release audit exist. Same-manifest rights flags, hashes, URLs, or status text can never authorize a future preview.
