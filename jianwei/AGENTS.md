# Jianwei source rules

When changing Jianwei packaging, runtime, skills, or host integration, read
`ARCHITECTURE.md` before editing.

- Treat `skills/` as Jianwei-owned product behavior, `runtime/` as shared
  execution support, and `adapters/` as host discovery and registration only.
- Treat `distribution.json` and `VERSION` as assembly inputs. Generate release
  metadata and host catalogs from them and assembled skill frontmatter.
- Assemble Tanyuan from the sibling `tanyuan/` source through
  `scripts/build_tanyuan.py`; keep its implementation out of host adapters.
- Edit source files, then build into a new output directory. Files under
  `dist/` are generated release artifacts.
- Keep every host on the same assembled skill set and shared runtime behavior.
- Complete packaging changes only after the offline Jianwei tests, a clean
  build, and the DSH registration check pass.

