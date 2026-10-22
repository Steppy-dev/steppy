"""Versioned wire contracts Steppy shares with anything that consumes its runs.

These are *not* internal types. They are the published shape of what leaves a
Steppy run — today the results bundle, later the job API — and a consumer at a
different version of Steppy has to be able to read them. That inverts one house
rule and is worth stating plainly: every model here is ``extra="allow"``,
whereas ``models.py`` is ``extra="forbid"``. The config models protect a
hand-written file, where a typo must fail loudly. These protect a wire format,
where the requirement is the reverse — a newer producer's extra field must
survive an older consumer, not crash it.

Compatibility rules, enforced in code rather than by convention:

1. **Additive fields are always allowed**, and unknown ones are preserved.
2. **Unknown enum-ish values are data, not errors** — an artifact ``kind`` this
   version has never heard of is stored opaquely rather than failing ingestion.
3. **A consumer accepts version N and N−1**, expressed as an explicit tuple so
   a test and an error message can both read it.
4. **Absent keys default** — inherited from the screenshot sidecars, whose
   ``device_scale_factor`` is the worked example (absent means 1).

Two contracts live here: ``bundle`` (``manifest.json`` — what a run *produced*)
and ``report`` (``report.json`` — what a run *means*). Only ``bundle`` is
re-exported below, because both modules define ``accepts`` and ``schema_json``
and flattening them would collide; import ``steppy.contracts.report`` directly.
"""

from .bundle import (
    ACCEPTED_BUNDLE_VERSIONS,
    BUNDLE_SCHEMA_VERSION,
    BundleCoverage,
    BundleFile,
    BundleGit,
    BundleManifest,
    BundleRun,
    BundleSpec,
    BundleTest,
    accepts,
    parse_manifest,
)

__all__ = [
    "ACCEPTED_BUNDLE_VERSIONS",
    "BUNDLE_SCHEMA_VERSION",
    "BundleCoverage",
    "BundleFile",
    "BundleGit",
    "BundleManifest",
    "BundleRun",
    "BundleSpec",
    "BundleTest",
    "accepts",
    "parse_manifest",
]
