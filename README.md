# Quiet LA web foundation

This is the local Next.js App Router + TypeScript + MapLibre foundation for the one-user-facing Noise experience. It follows the deployed Quiet LA portal shell documented in [`DESIGN_PARITY.md`](DESIGN_PARITY.md).

## Local preview

```sh
npm run stage:local
npm run dev
```

The local stage copies only the accepted v3 display payloads into `public/_local-data/`. It is ignored and hash-bound. No symlinks are accepted. The source families remain separate; `ALL DATA` is visual co-display only and the compatible acoustic sum is disabled.

## Validation

```sh
npm run typecheck
npm run lint
npm run test:contracts
npm run test:browser
npm run build:local
```

`npm run build` is the external/clean build. It fails closed when `public/_local-data/` exists, and only builds the payload-free static shell when the local stage is absent. `npm run manifest:clean` writes the deterministic code/tests/schema-only manifest under `release/`.

The useful preview is explicitly local-only until terms, authentication, and external platform admission are separately recorded. It does not claim measured/current/live noise, dBA/CNEL, quietness, or address prediction. Airport and Metro records are context only; Source-341 is incomplete — not computable.
