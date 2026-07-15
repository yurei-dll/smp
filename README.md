# SMP modpack builder

This repository turns a Prism Launcher instance into stable, reviewable inputs
for several Minecraft pack profiles. It reads Prism's packwiz index, inspects
installed JAR metadata, applies persistent classifications, and writes one
catalog per pack group.

## Repository layout

```text
pack/
  catalog/                       Generated categorized mod lists
  profiles/                      Pack composition definitions
  classification-overrides.json Persistent human classification decisions
scripts/import-prism             Local command-line entrypoint
scripts/apply-review             Persist completed review decisions
src/prism_list_builder.py        Import and classification implementation
tests/                           Offline unit tests
```

The planned JAR and platform-metadata identification rules are specified in
[docs/CLASSIFICATION.md](docs/CLASSIFICATION.md). That document also defines
the review-and-rerun workflow for classifications that are not high confidence.

## Import a Prism instance

The simplest path is to name the instance. The importer locates Prism's data
directory, reads its `.index/*.pw.toml` metadata, and scans the corresponding
JARs directly:

```bash
./scripts/import-prism --instance "Create Mega Pack"
```

Prism installs in the standard native Linux, Flatpak, Snap, macOS, and Windows
locations are searched automatically. For a portable or custom installation:

```bash
./scripts/import-prism --instance "Create Mega Pack" \
  --prism-root /path/to/PrismLauncher
```

Managed mods get their stable platform IDs and versions from Prism's packwiz
index. JARs absent from that index are still scanned and sent to review when
their pack policy cannot be established.

Local scanning does not make network requests. To enrich results with current
Modrinth project policy, opt in explicitly:

```bash
./scripts/import-prism --instance "Create Mega Pack" --modrinth
```

This sends each identified Modrinth JAR's SHA-512 hash to the Modrinth API for
exact-file resolution, followed by its project ID for environment metadata.

The generated files are written to `pack/catalog/`. Mods without a persistent
classification are written to `review.json`; this is intentional and prevents
the importer from guessing whether a mod is required or merely preferred.

Classify entries using a Modrinth project ID, filename, or exact mod name:

```json
{
  "core": ["jtmvUHXj"],
  "client-optional": ["BetterF3"],
  "server-curated": ["AI-Improvements-1.21-0.5.3.jar"],
  "ignored": []
}
```

Then rerun the import. Existing decisions continue to apply to later exports.

## Apply review decisions

Entries that need review contain an editable `designated_profile` property and
the complete list of allowed values:

```json
{
  "name": "Example Mod",
  "project_id": "example-project-id",
  "designated_profile": null,
  "allowed_profiles": [
    "core",
    "client-optional",
    "server-required",
    "server-curated",
    "ignored"
  ]
}
```

Set `designated_profile` to one allowed string, save the file, and apply all
completed decisions:

```bash
./scripts/apply-review
./scripts/import-prism --instance "Create Mega Pack"
```

The apply command validates every decision before writing, adds stable project
IDs to `pack/classification-overrides.json`, and removes applied entries from
the review list. Entries whose designation remains JSON `null` are untouched.

## Development

```bash
python3 -m unittest discover -s tests -v
```
