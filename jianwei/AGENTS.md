# Jianwei source rules

When changing Jianwei packaging, runtime, skills, or host integration, read
`ARCHITECTURE.md` before editing.

- Treat `skills/` as Jianwei-owned product behavior, `runtime/` as shared
  execution support, and `adapters/` as host discovery and registration only.
- Treat `distribution.json` and `VERSION` as assembly inputs. Generate release
  metadata and host catalogs from them and assembled skill frontmatter.
- Assemble Tanyuan from the sibling `tanyuan/` source through
  `scripts/build_tanyuan.py`; keep its implementation out of host adapters.
- Edit source files, then build into a new temporary output directory. The
  repository-root `plugin/` tree is the reviewed, tracked release artifact;
  never edit or overwrite it in place. Validate and compare the fresh output
  before replacing `plugin/`.
- Keep the release-shaped files at the repository root unchanged while they
  provide v0.1.1 compatibility. They are not inputs to future builds.
- Keep every host on the same assembled skill set and shared runtime behavior.
- Complete packaging changes only after the offline Jianwei tests and
  `python qa/run.py --suite build-contract` pass, including the packaged-payload
  invariant in `ARCHITECTURE.md`.
