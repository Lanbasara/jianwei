# Jianwei repository rules

Read `jianwei/ARCHITECTURE.md` before changing packaging, runtime, skills, or
host integration.

## Development workflow

For each new development request in a new conversation, fetch `origin` and
create a fresh task branch or worktree directly from the latest `origin/main`.
Before the first edit, verify that `HEAD` equals `origin/main` and the task
worktree is clean. Use `codex/<task-slug>` unless the user specifies a branch;
reuse an existing branch only when the user explicitly asks to continue it.

Keep the canonical `main` worktree on `main` and clean. Make changes and commits
only on the task branch. After the task is finished and its work is preserved
remotely, remove the local task worktree and branch.

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

Every new `.py`, `.mjs`, `.js`, `.sh`, `.bash`, or `.zsh` file under `qa/` is a
QA entry point by default, as is any executable file with another extension,
and must be registered explicitly in `qa/suites.json`; exact support-file
exclusions require a matching explanation in `qa/README.md`. Always pass
`python qa/policy_check.py`. Do not add commands to the registry: runner IDs
are implemented only in `qa/run.py`. AI evaluation remains local and
non-gating; live checks remain manual and require explicit network approval.

Complete source or packaging changes only after running the offline Jianwei
unit tests, producing a fresh build, checking the DSH registration contract,
comparing the reviewed generated tree with that fresh build, and passing the
offline Marketplace contract. Marketplace candidates must be generated from
committed `plugin/` metadata through `scripts/build_jianwei_marketplace.py`;
never edit a catalog candidate by hand or fall back to the compatibility
package at the repository root. The builder must authenticate the complete
tagged `plugin/` tree against its manifest and must verify imported catalog
history against the same local Git repository before publishing a candidate.
Source release artifacts must be produced by `scripts/build_jianwei_release.py`
from committed `plugin/` Git objects and a committed Marketplace `origin/main`
index. A workflow-dispatch build is a preview, never a formal release. Only a
stable version tag contained in `origin/main` may reach the protected
`source-release` environment. Marketplace candidates remain review artifacts;
this repository never writes, opens a pull request against, or merges the
Marketplace repository automatically.

The prohibition above applies to repository CI and release workflows. The internal
maintainer Skill `.agents/skills/jianwei-release/` may update the Marketplace only after
an exact user-confirmed release card; it must still use a separate protected Marketplace
PR and may not grant that capability to the source release workflow.

AI-dependent quality evaluation is advisory and local-only. It must be
explicitly classified and must never become a pull-request, release, install,
or upgrade gate. Deterministic offline checks may be mandatory. Live network
checks and credentialed data-source checks require separate opt-in execution.
Do not rewrite `qa/tanyuan/frozen.json` or its locked files as a side effect of
ordinary source changes; changes to frozen evidence require an explicit review.
