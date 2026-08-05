# Quiet LA portal parity

This app follows the deployed portal shell rather than inventing a new visual system.

## Source of truth

- `implementation/outputs/deploy/quiet-la-v2.1-dense/index.html`: QL mark, Quiet LA identity, glass instrument composition, D/E/N control, legend/disclosure language, inspection card, zoom stack, model-limits dialog.
- `implementation/outputs/deploy/quiet-la-v2.1-dense/app.css`: layout proportions, `Avenir Next`/system typography strategy, paper/ink/indigo tokens, glass treatment, responsive breakpoints, focus and reduced-motion behavior.
- `implementation/outputs/deploy/quiet-la-v2.1-dense/v21.css`: portal heat ramp, layer-style controls, compact type scale, glass card spacing, mobile sheet offsets.

## Implemented parity checklist

- [x] Warm paper field (`rgb(247 245 238)`), charcoal identity, indigo active state.
- [x] QL black rounded-square mark and Quiet LA / Noise portal lockup.
- [x] Translucent glass instruments, white border, 18px blur, portal shadow, 12/16/18/20px radii.
- [x] Master Noise view, D/E/N scenario period, Noise layers, legend, inspection card, zoom/fit stack.
- [x] Teal → yellow → rust → red relative-result ramp and context-only legend replacement.
- [x] Bottom disclosure rail and attribution above it.
- [x] Responsive mobile sheets, keyboard focus, reduced-motion and high-contrast handling.

The new code replaces only the old static data/runtime path with MapLibre, typed layer contracts, local hash-bound staging, and separated source-family records.
