# Mod identification and classification

This document defines the intended classification process for mods imported
from Prism Launcher. It is a design contract for future scanner work; the
current importer does not inspect JAR contents or query mod platforms yet.

## Objective

The importer must minimize repetitive manual work without silently putting a
mod into an unsafe pack. Its normal workflow is:

```text
Prism JSON export + instance mods directory
                    |
                    v
         collect identification evidence
                    |
          +---------+----------+
          |                    |
          v                    v
 high-confidence result   medium/low/unknown result
          |                    |
          v                    v
 classified catalog        review.json
                               |
                               v
                classification-overrides.json
                               |
                               v
                       rerun importer
```

Only high-confidence results may be classified automatically. Medium-, low-,
and unknown-confidence results must be written to the review list. After a
person adds those decisions to `pack/classification-overrides.json`, rerunning
the importer must remove them from review and place them in the selected lists.

Manual classification is authoritative. It must take precedence over every
platform response, JAR declaration, heuristic, and previous generated result.

## Two separate questions

Classification must not confuse these questions:

1. **Runtime environment:** Can the file load on a client, dedicated server,
   or both?
2. **Pack policy:** Is it required, optional, curated, or intentionally
   ignored in this particular pack?

A JAR can provide strong evidence that it is client-only while providing no
evidence about whether this pack considers it required or optional. Automatic
assignment to a final group therefore requires strong evidence for both
questions. Otherwise the mod goes to review.

The final groups are:

| Group | Runtime placement | Pack policy |
| --- | --- | --- |
| `core` | Client and server | Required on both |
| `client-required` | Client | Required for this client pack |
| `client-optional` | Client | Optional client enhancement |
| `server-required` | Dedicated server | Required server component |
| `server-curated` | Dedicated server | Selected server QoL or performance mod |
| `ignored` | Neither output | Deliberately excluded |

## Evidence collection

The future importer should accept the exported list and the Prism instance's
actual mod directory:

```bash
./scripts/import-prism prism-mods.json \
  --mods-dir ~/.local/share/PrismLauncher/instances/example/minecraft/mods
```

It should match each export entry to a JAR by exact filename, calculate a file
hash, and inspect the JAR as a ZIP archive without executing its code.

### Fabric

Read `fabric.mod.json` from the JAR root and record:

- top-level `environment`: `client`, `server`, or `*`;
- `main`, `client`, and `server` entrypoints;
- sided mixin declarations;
- required, recommended, and conflicting dependencies;
- mod ID and declared version.

An explicit top-level `environment` value is a loader-enforced runtime signal.
Entrypoints and mixin sides are supporting evidence, but do not necessarily
describe the environment of the complete mod.

### Forge and NeoForge

Read whichever supported metadata files are present, including:

```text
META-INF/mods.toml
META-INF/neoforge.mods.toml
```

Record:

- mod IDs and versions;
- an explicit `clientSideOnly` declaration when supported by that loader;
- `displayTest` or its current equivalent;
- declared dependencies, including dependency-side restrictions;
- loader and Minecraft version constraints.

`displayTest` is network compatibility evidence, not an instruction about
which physical environment loads the mod. Likewise, the side of a dependency
does not establish the side of the containing mod. Neither is sufficient by
itself for automatic runtime classification.

### Platform metadata

When a Prism URL identifies a Modrinth or CurseForge project, query the exact
file/version where possible and record:

- project and version IDs;
- supported Minecraft versions and loaders;
- client-side and server-side support declarations;
- exact download filename, URL, and hashes;
- required and optional dependency relationships.

Platform metadata is valuable but external. Project-wide declarations may not
accurately describe every historical file, so exact-file evidence is stronger
than a project-level statement.

### Bytecode and naming heuristics

The scanner may record indicators such as client-only Minecraft package
references, rendering entrypoints, or conventional names. These signals must
remain low confidence. A class can be safely guarded behind a side check, and
a filename is not a runtime contract.

## Confidence rules

Every proposed classification should include its evidence and confidence:

```json
{
  "filename": "example.jar",
  "proposed_group": "client-optional",
  "confidence": "medium",
  "evidence": [
    {
      "source": "modrinth-project",
      "field": "server_side",
      "value": "unsupported"
    }
  ]
}
```

### High confidence

High confidence requires non-conflicting, authoritative evidence for both the
runtime environment and final pack policy. Examples include:

- a manual override;
- loader-enforced client/server metadata combined with exact-file platform
  metadata that clearly distinguishes required from optional;
- agreement between exact-file metadata and explicit loader metadata, with an
  unambiguous mapping to a pack group.

If authoritative sources disagree, the result is not high confidence and must
be reviewed.

### Medium confidence

Examples include:

- project-wide platform environment metadata without exact-file confirmation;
- client-only entrypoints without an explicit whole-mod environment;
- `displayTest`, dependency sides, or similar compatibility declarations;
- strong runtime evidence without evidence for required-versus-optional pack
  policy.

Medium-confidence proposals go to `review.json` and are never applied
automatically.

### Low confidence

Examples include:

- bytecode references to client or server packages;
- mod name, filename, description, or category keywords;
- incomplete or internally inconsistent metadata.

Low-confidence proposals go to `review.json` with their evidence so a person
can decide quickly.

### Unknown

Use `unknown` when the JAR cannot be found, metadata cannot be parsed, the mod
platform cannot be identified, or no useful environment evidence exists.
Unknown results follow the same manual review path.

## Review output

`pack/catalog/review.json` should contain enough information to make a decision
without reopening the JAR manually:

```json
[
  {
    "filename": "example.jar",
    "name": "Example",
    "project_id": "example-project-id",
    "proposed_group": "client-optional",
    "confidence": "medium",
    "reason": "Client-only platform declaration lacks exact-file confirmation",
    "evidence": []
  }
]
```

The reviewer adds a stable selector to the desired group:

```json
{
  "client-optional": ["example-project-id"]
}
```

Project IDs are preferred because they remain stable when filenames and
versions change. Exact names and filenames remain supported for mods without a
known platform project.

On the next run, the importer must:

1. apply the manual override first;
2. omit that mod from `review.json`;
3. write it to the manually selected group;
4. retain collected evidence for diagnostics;
5. fail clearly if the same selector appears in multiple groups.

This loop repeats only for newly encountered or newly ambiguous mods. Existing
manual decisions remain stable across Prism exports and mod updates.

## Runtime verification

Static inspection reduces the review set but cannot prove that a mod is safe
on a dedicated server. A later build stage should start the assembled server
pack with the exact loader and Minecraft versions in a temporary directory.
Loader failures, missing dependencies, client-class linkage errors, and mixin
failures should fail validation.

A successful start proves that the tested combination reached the chosen
readiness marker. It does not determine whether each included mod is required,
so runtime verification must not rewrite manual pack-policy decisions.
