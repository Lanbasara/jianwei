# Jianwei repository rules

Read `jianwei/ARCHITECTURE.md` before changing packaging, runtime, skills, or
host integration.

## Repository boundaries

- `jianwei/` is the Jianwei-owned source layer.
- `tanyuan/` is a sibling source component with its own implementation boundary.
  Jianwei consumes it only through `scripts/build_tanyuan.py` and
  `scripts/build_jianwei.py`.
- `qa/jianwei/` contains offline distribution and host-contract checks.
- `qa/tanyuan/` contains offline Tanyuan regression checks and frozen historical
  evaluation tools. Third-party raw golden evidence is local-only, may have
  redistribution restrictions, and is loaded only from an explicit absolute
  `JIANWEI_QA_HOME`; it never belongs in Git or a plugin build.
- `plugin/` is generated, reviewed, and tracked. Never edit it by hand or
  overwrite it in place; build to a new temporary directory, validate it, and
  replace the tree only after validation succeeds.
- The release-shaped files at the repository root preserve the historical
  v0.1.1 root-layout installation during migration. They are compatibility
  material, not the source of future changes.

Do not copy credentials, `.env`, virtual environments, caches, reports, or
evaluation data into `plugin/`. Product inputs and output paths must remain
explicitly allowlisted, bounded to their declared roots, and free of symbolic
link traversal. Python subprocesses must use argument arrays with `shell=False`.

## Validation

Complete source or packaging changes only after running the offline Jianwei
unit tests, producing a fresh build, checking the DSH registration contract,
and comparing the reviewed generated tree with that fresh build.

AI-dependent quality evaluation is advisory and local-only. It must be
explicitly classified and must never become a pull-request, release, install,
or upgrade gate. Deterministic offline checks may be mandatory. Live network
checks and credentialed data-source checks require separate opt-in execution.
Do not rewrite `qa/tanyuan/frozen.json` or its locked files as a side effect of
ordinary source changes; changes to frozen evidence require an explicit review.
