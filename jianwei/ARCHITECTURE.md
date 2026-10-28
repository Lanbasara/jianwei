# Jianwei architecture

Jianwei is one release bundle for Claude Code, Codex, and DSH. The hosts install
the same product capabilities and shared runtime; small host-specific files only
adapt discovery and registration.

## Source layers

### Product capabilities

`skills/` contains skills owned by Jianwei. Tanyuan remains independently
maintained in the workspace sibling `tanyuan/` tree and is assembled as
`skills/tanyuan/` by `scripts/build_jianwei.py`. The assembled skill frontmatter
is the source for generated host catalogs.

### Shared runtime

`runtime/` contains setup and launch behavior used identically by every host.
The builder maps these source files to their stable release paths under
`scripts/` and `skills/tanyuan/bin/`.

### Host adapters

`adapters/<host>/` contains only the files needed to expose the common product
to that host:

- `claude/` defines the Claude Code compatibility manifest.
- `codex/` defines the Codex compatibility manifest.
- `dsh/` defines the package metadata, Cordis patch, and skill registrar.

JSON templates use exact `$TOKEN` values supplied by the builder. Adapters may
describe identity, paths, compatibility, and registration. Product behavior,
credentials, network access, and copies of skill instructions belong elsewhere.
The DSH template uses `$FILES`, derived from approved assembly outputs; it does
not maintain a separate hand-written resource list.

## Assembly interface

`distribution.json` declares the product identity, ordered skill set, shared
file mappings, and adapter outputs. `VERSION` is the only Jianwei version source.
The builder validates those inputs, builds Tanyuan, copies Jianwei-owned skills
and runtime files, renders adapters, and generates `host/catalog.json` and
`manifest.json`.

The release layout remains host-native rather than exposing the source layers:

```text
plugin.json
.claude-plugin/plugin.json
.codex-plugin/plugin.json
package.json
cordis.patch.yml
host/{dsh.mjs,catalog.json}
scripts/{setup.py,runtime.py}
skills/{setup,tanyuan,research,portfolio,advise,report,research-then-report}
references/portfolio-snapshot.md
```

The repository-root `plugin/` directory contains the reviewed, tracked release
tree. Build into a new temporary directory rather than editing or overwriting
`plugin/` in place. Run the offline checks against that fresh output, compare it
with `plugin/`, and replace the tracked tree only after the fresh output passes.
The release-shaped files at the repository root temporarily preserve the
v0.1.1 installation layout and are not assembly inputs.

## Invariants

1. Claude Code, Codex, and DSH expose the same assembled skills.
2. Skill text and shared runtime behavior do not vary by host.
3. Host adapters contain no `SKILL.md` or data/research implementation.
4. Release inputs are explicit allowlists; development, QA, report, cache, and
   credential files do not enter the bundle.
5. Generated manifests agree on product identity and version.
6. Packaging preserves the entire approved payload: identical file paths,
   bytes, and permissions. A resource present in the build but absent after
   package-manager filtering is a failed release contract. New resources enter
   through the assembly inputs; host adapters inherit that inventory.

Verify these invariants with:

```bash
# Point this at Python 3.10+ with the exact versions in tanyuan/requirements.txt.
export JIANWEI_TEST_PYTHON=/absolute/path/to/prepared/python
PYTHONDONTWRITEBYTECODE=1 "$JIANWEI_TEST_PYTHON" -m unittest discover -s qa/jianwei -v
"$JIANWEI_TEST_PYTHON" qa/run.py --suite build-contract
```

The build contract compares a fresh build with `plugin/`, runs an offline
`npm pack` with lifecycle scripts disabled, verifies and unpacks its exact
payload, then runs DSH registration against that unpacked directory. This
checks the package-manager filtering seam used by DSH; it does not claim to
test a live host session or model instruction-following. It runs in both the
pull-request and release gates and requires local Node.js and npm.
