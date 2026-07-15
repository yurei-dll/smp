# SMP modpack builder

This repository turns mod lists exported by Prism Launcher into stable,
reviewable inputs for several Minecraft pack profiles.

The first implemented layer is deliberately offline: it parses Prism JSON,
normalizes and deduplicates entries, applies persistent classifications, and
writes one list per pack group. Resolving exact downloads and generating
packwiz metadata will be added after the import format has been exercised with
real exports.

## Repository layout

```text
imports/                         Local Prism exports (ignored by Git)
pack/
  catalog/                       Generated categorized mod lists
  overrides/                     Future pack files and configs
  profiles/                      Pack composition definitions
  classification-overrides.json Persistent human classification decisions
scripts/import-prism             Local command-line entrypoint
src/prism_list_builder.py        Import and classification implementation
tests/                           Offline unit tests
```

## Import a Prism mod list

In Prism, open the instance's **Mods** page, select **Export List**, choose
JSON, enable Filename, Version, and URL, then save the result.

```bash
./scripts/import-prism ~/Downloads/Create-Mega-Pack.json
```

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

## Development

```bash
python3 -m unittest discover -s tests -v
```
