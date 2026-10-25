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
skills/{tanyuan,research,report,research-then-report}
```

`dist/` contains generated artifacts. Build into a new directory rather than
editing or overwriting an existing release.

## Invariants

1. Claude Code, Codex, and DSH expose the same assembled skills.
2. Skill text and shared runtime behavior do not vary by host.
3. Host adapters contain no `SKILL.md` or data/research implementation.
4. Release inputs are explicit allowlists; development reports, QA, cache, and
   credential files do not enter the bundle.
5. Generated manifests agree on product identity and version.

Verify these invariants with:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s qa/jianwei -v
python3 scripts/build_jianwei.py --out /tmp/jianwei-check
node qa/jianwei/check_dsh.mjs /tmp/jianwei-check
```

