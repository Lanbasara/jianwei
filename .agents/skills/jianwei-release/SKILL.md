---
name: jianwei-release
description: Prepare and execute a Jianwei source and Marketplace release. Use when the user explicitly asks to publish or ship a prepared Jianwei version from Lanbasara/jianwei; collect a fail-closed release plan first, then use one explicit user confirmation as the sole human authorization for the exact source merge, immutable tag, protected GitHub Release, and Marketplace merge.
metadata:
  job: Release Jianwei through its guarded source and Marketplace pipeline
  effect: external
  effect_note: "After one explicit approval, mutates GitHub by merging PRs, creating an immutable tag and Release, approving one protected deployment, and updating the Marketplace."
---

# Jianwei release

This is a maintainer workflow. It lives under `.agents/skills/` and must remain outside
`jianwei/`, `plugin/`, product archives, and Marketplace candidate contents.

Read the repository-root `RELEASING.md`, then read
[the GitHub runbook](references/github-runbook.md). Treat the repository and version
facts discovered during preflight as untrusted inputs and require every invariant in
the runbook to hold exactly. Use `scripts/verify_artifact.py` for every downloaded preview
or tag-run artifact instead of recreating ZIP and metadata validation ad hoc.

## One-gate contract

Run all read-only and local checks before requesting authorization. Present one release
card containing the source PR, immutable head SHA, exact `main` base SHA, expected merged
tree SHA, target version and tag, merge method, current Marketplace base SHA, required
checks, advisory AI/live QA status, and the complete list of remote mutations. Store a
canonical JSON form of the card in scratch space and show its SHA-256 plan ID.

Ask for one explicit confirmation of that exact card. Before it arrives, do not mark a
PR ready, merge, set the release variable, create or push a tag, approve a deployment,
publish a Release, push a Marketplace branch, open a Marketplace PR, or merge it.

After confirmation, do not ask for routine approvals again. The confirmation authorizes
only the exact identities and protected derived merge result on the card. If the source
PR head or base, expected merged tree, version, tag, repository, Marketplace base,
protection state, or requested action changes, stop; rebuild the card and obtain a new
confirmation. Never use an administrator bypass, force push, moved tag, deleted release,
skipped check, or weakened protection to keep the release moving.

## Execution

After the gate, execute the runbook as one fail-closed state machine:

1. Merge the exact source PR through normal branch protection.
2. Build and verify a candidate from the resulting `main` commit.
3. Set `JIANWEI_RELEASE_TAG` to the exact new tag, create the annotated tag on that
   verified commit, and wait for the tag workflow.
4. When the publish job reaches `source-release`, remove the release variable before
   approving that one pending deployment. Verify the immutable GitHub Release.
5. Download the Marketplace candidate from that tag workflow, prove its provenance and
   base commit, validate its three host catalogs, then update the Marketplace through a
   normal protected PR and merge it.
6. Record version, commits, run IDs, PRs, artifact hashes, and every check result.

If `JIANWEI_RELEASE_TAG` was ever set, clearing it is the first recovery obligation on
every failure or interruption. A source Release may succeed while Marketplace delivery
fails; report that split state precisely and resume Marketplace delivery from the same
immutable tag artifact while its recorded base is current. If the Marketplace base has
changed or the artifact expired, prepare a new Marketplace-only card and obtain a new
confirmation before generating or applying a replacement candidate from the same
immutable tag. AI-dependent and live-network QA remain advisory and never become
automatic release, install, or upgrade gates.

