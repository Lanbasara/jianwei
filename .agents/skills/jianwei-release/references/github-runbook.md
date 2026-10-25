# GitHub release runbook

This runbook operates only on these repositories:

- source: `Lanbasara/jianwei`
- distribution catalog: `Lanbasara/jianwei-marketplace`

Use `gh` structured JSON output and Git commands with explicit repositories, refs, and
full SHAs. Never expose authentication material in output. Scratch checkouts and
artifacts belong in a fresh private temporary directory, not in either repository.

On every fresh or resumed invocation, first read `JIANWEI_RELEASE_TAG`, the target tag,
the matching workflow run, and the GitHub Release. If the variable is non-empty and no
actively confirmed plan owns it, delete it and verify deletion before doing anything
else. Reconstruct the state from remote facts; never assume an interrupted prior step
completed.

## Preflight: no remote mutations

Establish all of the following before presenting the release card:

1. `gh auth status -h github.com` succeeds for the intended maintainer. The account can
   read Actions, merge both repositories, edit repository variables, and approve the
   `source-release` environment. Authentication setup happens before a release session;
   it is not part of the release gate.
2. The source checkout has the exact GitHub origin, a clean worktree, and no untracked
   release inputs. Fetch the remote without changing local branches.
3. Select one open source PR into `main`. Record its number, URL, full head SHA, base SHA,
   draft state, mergeability, unresolved discussions, and check conclusions. Require
   `qa (3.10)` and `qa (3.12)` to be successful. A missing, pending, stale, skipped, or
   neutral required check is not success. Fetch both commits and compute the expected
   merge tree for that exact base+head pair with `git merge-tree --write-tree`; a conflict
   or missing deterministic tree blocks the plan.
4. Read the version from the PR head's committed `jianwei/VERSION`. Require the same
   stable SemVer in committed `plugin/package.json` and `plugin/manifest.json`; set the
   proposed tag to `v<version>`. Reject an existing remote tag, GitHub Release, or
   Marketplace version entry.
5. Run the repository's policy check and release gate in its prepared dependency
   environment. Verify the committed `plugin/` tree against a fresh build. Advisory AI
   or live QA may be run locally when requested; list each one as pass, fail, or not run.
6. Read the active `main` and `v*` rulesets, release immutability setting, and
   `source-release` environment. Require the documented protections and confirm that the
   current user can approve the environment without an administrator bypass. If
   prevent-self-review or another reviewer requirement makes that false, the promised
   one-confirmation path is unavailable and preflight stops before showing a card.
7. Require repository variable `JIANWEI_RELEASE_TAG` to be absent or empty. Record the
   current `jianwei-marketplace` `main` full SHA and ensure its index has no target
   version.

The release card must enumerate these post-confirmation mutations:

- ready and squash-merge the exact source PR without `--admin`;
- dispatch and inspect the source candidate workflow;
- set the exact-tag release variable, create and push one annotated tag;
- clear the variable, approve one `source-release` deployment, and publish the Release;
- push one Marketplace release branch, open its PR, and merge it after checks.

Serialize the complete card as canonical JSON with sorted keys and no secrets. Include
repository names, PR number and URL, source head SHA, source base/current-main SHA,
expected merged tree SHA, version, tag, Marketplace base SHA and index digest, checks,
artifact hashes when available, advisory QA status, mutation list, and creation time.
Hash those exact bytes with SHA-256. Ask the user to confirm that plan ID and tag; a reply
belongs only to the currently displayed card. Context compression, target drift, or a
different plan ID invalidates it.

## Source merge and candidate

Re-read the source PR immediately after confirmation. Its head SHA, base SHA/current
`main`, computed merge tree, and all card facts must be unchanged. Mark it ready if
needed, then squash-merge it with
`--match-head-commit <approved-head-sha>` and without an administrative bypass. Wait for
the protected merge to finish and fetch the resulting full `origin/main` SHA. Require
that commit have the approved base as its sole parent and that its tree SHA equal the
approved expected merge tree; the new commit is the authorized protected derivative of
the exact base+head pair. A mismatch means the source PR was merged but release has not
started; stop before any release variable or tag and build a new plan.

Before dispatching the preview, independently run the deterministic release builder from
that verified derived merge commit and the card's exact Marketplace base checkout. Record
the local product and candidate SHA-256 values and verify the local candidate's exact
content contract. These hashes are deterministic consequences of the already confirmed
base, head, expected merge tree, and Marketplace base; they are post-merge verification
evidence, not new authorization fields and do not change the plan ID. Never derive the
expected remote-artifact digest from the remote artifact under test.

Dispatch `.github/workflows/release.yml` on that exact `main` through the workflow
dispatch REST API and retain its returned run ID. Verify event `workflow_dispatch` and
exact head SHA, not merely recency. Wait for every job to
complete successfully and download only its artifact named
`jianwei-source-release-<main-sha>`.

Verify `SHA256SUMS`, then parse `release.json` with an exact schema expectation. Require:

- `official` is false;
- `version` is the approved version;
- `commit` is the merged `main` SHA;
- product and Marketplace filenames and SHA-256 values match the downloaded files;
- Marketplace `baseRepository` and `baseCommit` equal the approved repository and still-
  current Marketplace `main`.

Any mismatch ends the release before a tag exists.

Require the downloaded preview product and candidate to be byte-for-byte equal to the
independently built local files. Run the Skill's `scripts/verify_artifact.py` with the
approved version, merged source commit, Marketplace base, card-bound Marketplace
base-index digest, independently generated local candidate digest, and `--official
false`. Retain its structured result and product digest for the tag-run comparison. The
verifier arguments must include
`--expected-marketplace-index-sha256` and `--expected-marketplace-sha256`; values read
only from the artifact under test are not independent expectations.

## Exact-tag production window

Set repository variable `JIANWEI_RELEASE_TAG` to the approved tag. This value authorizes
only a workflow whose `github.ref_name` is exactly equal to that tag. From this point,
clearing the variable is an unconditional cleanup obligation.

Create one annotated tag at the verified merged `main` SHA and push that exact ref. Do
not reuse, move, delete, or force a tag. Identify the resulting `push` workflow by exact
tag and head SHA, then wait for `qa` and `build` to succeed.

Before approving the environment, download the tag run's build artifact and repeat all
checksum, exact-schema, ZIP inventory, source commit, version, official tag, and current
Marketplace base checks. Require `official` true. Compare the product bytes with the
verified preview; allow Marketplace candidate differences only where the formal tag/ref
is expected. A mismatch stops with the immutable tag retained and no Release published.
Independently run the deterministic release builder from the immutable source tag and
the card-bound Marketplace base checkout. Require that locally generated candidate's
SHA-256 to equal the tag-run candidate. Use `scripts/verify_artifact.py` with
`--official true`, the exact `--tag`, the preview's product digest as
`--expected-product-sha256`, the locally generated candidate digest as
`--expected-marketplace-sha256`, and the card-bound committed index digest as
`--expected-marketplace-index-sha256`.

Before any environment approval, verify the remote tag through the Git data API. The
`git/ref/tags/<tag>` object must have type `tag`; read that tag object and require its
target type to be `commit` and its target SHA to equal the approved merged source SHA.
A lightweight tag or any mismatch permanently stops this version's publication.

Poll the run's pending deployments through:

`GET /repos/Lanbasara/jianwei/actions/runs/<run-id>/pending_deployments`

Require exactly one pending deployment named `source-release`, with
`current_user_can_approve` true. Delete `JIANWEI_RELEASE_TAG` before approving it; if
deletion succeeds, an immediate exact GET must return HTTP 404. If deletion fails, set
it to the fail-closed sentinel `false` and require an immediate exact GET value of the
literal string `false`. Re-check the same exact absent-or-literal-`false` state
immediately before the approval request. Approve
only that environment ID through the same run endpoint with state `approved` and a
release-specific comment. This API approval is covered by the user's single release-card
confirmation; it is not a second human gate.

Wait for the workflow to succeed. Repeat the remote annotated-tag object/type/target
verification after publication. Verify the GitHub Release exists at the exact
tag, is immutable, is neither draft nor prerelease, and contains only `jianwei-plugin.zip`,
`release.json`, and `SHA256SUMS`. Download them, verify checksums again, and record the
attestation and run URL. Do not rely on `targetCommitish` alone to prove the commit. The
Marketplace candidate remains an Actions artifact.

## Marketplace delivery

Download `jianwei-marketplace-candidate.zip` from the successful tag workflow, not from
an unrelated run. Verify it against that run's `SHA256SUMS` and `release.json`; require
`official` true, the exact tag and source commit, and a Marketplace base commit still
equal to remote Marketplace `main`.

Inspect the ZIP inventory before extraction. It must contain exactly these four regular
files: `jianwei-marketplace/.claude-plugin/marketplace.json`,
`jianwei-marketplace/.agents/plugins/marketplace.json`,
`jianwei-marketplace/dsh/index.json`, and `jianwei-marketplace/README.md`; reject absolute
paths, links, traversal, duplicate names, extra paths, or oversized metadata.
Extract into a fresh temporary directory.

Create a fresh Marketplace worktree or clone at the recorded base commit. Copy the
candidate directory contents into it, then require the diff to touch only those catalog
files. Re-run the Marketplace contract and the configured Claude/DSH validators. Confirm
all three catalogs name the same Jianwei version, tag, source commit, and `plugin`
subdirectory.

Commit on `release/jianwei-v<version>`, push without force, and open a Marketplace PR to
`main`. Wait for all required checks and discussions, require the PR head and base to
remain exact, then squash-merge without `--admin`. Finally verify remote Marketplace
`main` contains the expected version and source commit.

The `.agents/plugins/marketplace.json` file is a host catalog and is expected in the
Marketplace repository. The maintainer Skill at
`.agents/skills/jianwei-release/` is a different path and must never appear in a candidate
or product archive.

## Failure and completion

On any failure after setting `JIANWEI_RELEASE_TAG`, clear it before other recovery work.
Do not delete or retarget a pushed tag and do not edit an existing Release. Fix source
failures with a new PR and version. If the source Release succeeded but Marketplace
delivery failed, retain the immutable Release and resume only the Marketplace steps from
the same verified tag workflow artifact when its recorded base remains valid.

If Marketplace `main` has changed, the original confirmation is no longer valid. Build a
new Marketplace candidate from the same immutable source tag and the new committed
Marketplace `origin/main` using `scripts/build_jianwei_marketplace.py`; verify tag,
source commit, new base commit/index digest, output inventory, and all validators. Present
a new Marketplace-only card and plan ID authorizing only its branch, PR, and protected
merge. If the Actions artifact expired but Marketplace base is unchanged, regenerate the
candidate with deterministic `scripts/build_jianwei_release.py --ref <immutable-tag>`
against fresh source and Marketplace checkouts and require byte-for-byte equality with
any retained hash; absent that evidence, treat it as a new Marketplace-only plan. This
recovery never changes the source tag or GitHub Release.

Completion requires all of these facts: source PR merged; candidate run successful; tag
workflow successful; release variable absent/empty; immutable GitHub Release verified;
Marketplace PR merged; Marketplace `main` verified; hashes, run IDs, commit SHAs, PR URLs,
advisory QA status, and any skipped checks recorded in the final handoff.

## Critical command and API shapes

Use these shapes after substituting only values already validated and bound to the card:

- PR facts: `gh pr view <number> --repo Lanbasara/jianwei --json ...`
- ready: `gh pr ready <number> --repo Lanbasara/jianwei`
- merge: `gh pr merge <number> --repo Lanbasara/jianwei --squash --match-head-commit <head-sha>`
- preview: use `POST repos/Lanbasara/jianwei/actions/workflows/release.yml/dispatches`
  with `ref: main`, retain the returned `workflow_run_id`, and verify that run's identity
- run selection: `gh run list --repo Lanbasara/jianwei --workflow release.yml --event <event> --json databaseId,headSha,headBranch,status,conclusion,url`
- artifact: `gh run download <run-id> --repo Lanbasara/jianwei --name jianwei-source-release-<sha> --dir <fresh-dir>`
- arm: `gh variable set JIANWEI_RELEASE_TAG --repo Lanbasara/jianwei --body <tag>`
- disarm: `gh variable delete JIANWEI_RELEASE_TAG --repo Lanbasara/jianwei`
- pending deployment: `gh api repos/Lanbasara/jianwei/actions/runs/<run-id>/pending_deployments`
- environment approval: send a JSON object containing the single validated
  `environment_ids` value, `state: "approved"`, and a plan-specific `comment` to the same
  endpoint with `gh api --method POST --input <private-json-file>`.

Always inspect the structured response and assert the expected identity and state. Do not
select runs, environments, artifacts, or PRs solely by “latest”, display name, or timing.

