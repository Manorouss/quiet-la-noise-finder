# Notices and licensing

NoiseModelling is maintained by Université Gustave Eiffel and is distributed under GNU GPL version 3 or later. The patch in `noisemodelling-dp0/AttenuationCnossos_hf_dp0.patch` is an adaptation against `AttenuationCnossos.java` from upstream tag `v6.0.0`; it is marked `GPL-3.0-or-later`. The upstream source file and binary are not copied into this repository. The complete GNU GPL version 3 text is included as `LICENSE-GPL-3.0.txt` from <https://www.gnu.org/licenses/gpl-3.0.txt>. Upstream project information: <https://github.com/Universite-Gustave-Eiffel/NoiseModelling>.

The Python model sources are project-authored snapshots. This repository does not add a license grant where the upstream project source did not declare one; consult the owning repository's license before reuse. Names of public reference publishers and sources are recorded in `MANIFEST.json` for attribution. External publications, datasets, ANP tables, and captured observations are not redistributed by this bundle, and their license/terms continue to govern any separately authorized use.

No copied NoiseModelling source or third-party runtime library is included. Python checks use the Python standard library except where an individual aircraft ingest module imports `openpyxl`; that optional dependency is not vendored.
