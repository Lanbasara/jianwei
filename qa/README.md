# QA suites and gates

`qa/suites.json` is the only suite registry. Every suite declares exactly
`id`, `kind`, `execution`, `gates`, `network`, `credentials`, `runner`, and
`files`. File paths are explicit repository-relative paths; globs, missing
files, duplicate registration, path escape, and unknown values fail policy.

Every `.py`, `.mjs`, `.js`, `.sh`, `.bash`, and `.zsh` file under `qa/` is
treated as a suite entry point by default, regardless of its filename. A file
with any other extension is also an entry point when an executable bit is set.
The only exclusions are this exact, reviewed list:

- `qa/policy_check.py` and `qa/run.py`: registry and runner control plane.
- `qa/no_network/sitecustomize.py`: best-effort defense-in-depth Python network
  guard.
- `qa/jianwei/check_market.mjs`: adapter for a separately supplied official
  Marketplace validator, not a self-contained repository test.
- `qa/tanyuan/evidence.py`: local evidence path-resolution library.
- `qa/tanyuan/evals/score.py`: caller-driven diagnostic scorer.
- `qa/tanyuan/evals/build_golden.py`, `fetch_truth.py`, and
  `examples/ty_adapter.py`: frozen, retired audit tools.

Adding another support file requires an exact exclusion in
`qa/policy_check.py` and a matching explanation here. Names such as
`eval_model.py` or `score_model.py` do not bypass registration.

## Classification rules

- `ci` suites are offline, credential-free, and are the only suites eligible
  for the current pull-request gate. A release gate does not exist yet; its
  workflow, registry values, and tests must be added atomically.
- `live` suites are manual, networked, non-gating, and require an explicit
  `--allow-network` invocation.
- `ai-eval` suites must always be `execution=local`, `gates=[]`, and
  `credentials=external-local`. Adding an AI suite never upgrades it into a
  PR, release, install, or upgrade requirement.
- Local frozen-evidence verification is non-gating. Set `JIANWEI_QA_HOME` to
  an absolute existing local golden root. Raw third-party evidence stays out
  of Git and may have redistribution restrictions.

The registry contains runner identifiers, never commands, and a suite ID must
equal its runner ID. Each runner capability also fixes the exact ordered file
manifest; registry files cannot be swapped or expanded independently.
`qa/run.py` maps each identifier to reviewed argv arrays or an in-process build
comparison, runs subprocesses with `shell=False`, fixes the repository working
directory, and applies timeouts. Those fixed runners, exact file manifests,
and the gate selector are the enforcement boundary for reviewed CI work.

Every `network=false` suite receives a replaced (not appended) `PYTHONPATH`
that loads `qa/no_network/sitecustomize.py`, independently of its credential
classification. That hook blocks common Python IPv4/IPv6 resolution, connect,
bind, and datagram operations while allowing files, pipes, and Unix domain
sockets. It is only a best-effort defense against accidental access, not a
security boundary: direct `_socket` use, Python `-S`/`-I`, and non-Python
processes can bypass it.
The only Node invocation is the fixed `build-contract` implementation running
the reviewed `qa/jianwei/check_dsh.mjs`; the registry cannot add arbitrary Node
commands or files.

## Commands

```bash
python -m pip install -r tanyuan/requirements.txt
python qa/policy_check.py
python -m unittest discover -s qa/meta -p 'test_*.py' -v
python qa/run.py --list
python qa/run.py --gate pull-request

export JIANWEI_QA_HOME=/absolute/path/to/local/golden
python qa/run.py --suite evidence-local

# Explicit manual network authorization; never run by a gate.
python qa/run.py --suite tanyuan-live --allow-network
```

Without `--allow-network`, the live suite fails before starting tests. Gate
runs construct a minimal allowlisted environment rather than trying to name
and remove every possible secret. Unknown variables and credential-related
variables such as `PASSWORD`, `SSH_AUTH_SOCK`, `NETRC`, and
`GOOGLE_APPLICATION_CREDENTIALS` are not inherited. `HOME`, XDG directories,
and temporary directories point to a fresh private directory for the lifetime
of the whole gate or suite and are removed afterward. On a workstation where
the selected `node` is a Volta shim, only the fixed Node contract child derives
`VOLTA_HOME` from that selected executable so it can use the already-installed
runtime; Python and other suite children never receive it, and no Node
configuration variables are inherited.
The runner replaces `PYTHONPATH` with the no-network guard
path, then selects only registered `ci` suites. An individually selected
`credentials=none` suite gets the same isolation. `external-local` suites may
inherit explicitly provided local credentials and evidence paths and therefore
never gate CI; when such a suite is offline, it still receives the best-effort
no-network guard.
