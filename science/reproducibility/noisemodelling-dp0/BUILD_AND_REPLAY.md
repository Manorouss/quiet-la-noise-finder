# Rebuild the bounded H/F patch

The candidate patch applies only to NoiseModelling `v6.0.0`'s
`noisemodelling-propagation/src/main/java/org/noise_planet/noisemodelling/propagation/cnossos/AttenuationCnossos.java`.
The unmodified upstream file SHA-256 is
`0be8c6f744ec50ed4c68cfc8c639a205d04bc88dd9aa23c840e1eb7bfdea2c8f`;
the patch SHA-256 is
`0adeb1ca9cb29b2fd77cd481007491c5e0c856b833ddcc8ffbaec2060c301d07`.

In a fresh upstream checkout, confirm the tag and source before patching:

```sh
git clone --depth 1 --branch v6.0.0 https://github.com/Universite-Gustave-Eiffel/NoiseModelling.git /tmp/noisemodelling-v6
git -C /tmp/noisemodelling-v6 describe --tags --exact-match
shasum -a 256 /tmp/noisemodelling-v6/noisemodelling-propagation/src/main/java/org/noise_planet/noisemodelling/propagation/cnossos/AttenuationCnossos.java
```

The last command must print the source hash above. Then, from the checkout root:

```sh
git apply --check --directory=noisemodelling-propagation/src/main/java/org/noise_planet/noisemodelling/propagation/cnossos /path/to/science/reproducibility/noisemodelling-dp0/AttenuationCnossos_hf_dp0.patch
git apply --directory=noisemodelling-propagation/src/main/java/org/noise_planet/noisemodelling/propagation/cnossos /path/to/science/reproducibility/noisemodelling-dp0/AttenuationCnossos_hf_dp0.patch
mvn -pl noisemodelling-propagation -am -DskipTests package
```

For actual Java regression calls, set `NOISEMODELLING_CP` to the compiled propagation classes plus the module runtime dependencies, then run:

```sh
/path/to/science/reproducibility/noisemodelling-dp0/run_java_regressions.sh "$NOISEMODELLING_CP"
```

The Java harness compiles to a temporary directory and replays both included synthetic TSV suites: 259 H-branch calls and 1,073 F-branch calls. The checked review reports 43 supported H exact-zero cases; for F it reports 216 supported positive-height exact-zero cases, 72 both-zero cases unchanged, 768 positive-distance cases unchanged, and 17 unsupported-domain cases unchanged. The replay vectors are deliberately synthetic; no campaign ray table or output is included. The independent root compile/review is summarized in the parent bundle's `AUDIT_SUMMARIES.json`.

This is source-level reproduction for one numerical limit only. It does not run WPS, reproduce the one-receiver export, authorize propagation, validate all receivers/tiles, or establish field accuracy.
