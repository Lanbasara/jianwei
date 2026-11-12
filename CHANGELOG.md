# Changelog

## 0.4.0 - 2026-11-12

- Add `explain` to make AI-generated investment analysis understandable without
  adding investment judgments, invented probabilities, or fixed output templates.
- Add optional `research-then-explain`, `advise-then-explain`, and
  `reflect-then-explain` workflows. Explanation stays in the current agent context
  by default, with delegation chosen only when useful.
- Strengthen evidence-based forward judgments and proportionate revisions in
  research and advice, keeping business outlook, price, and action distinct.
- Preserve the HTML report workflow, installation layout, configuration, and
  bundled Tanyuan 0.5.0; no migration is required.

## 0.2.1 - 2026-10-25

- Fix checkout-free release publication by explicitly selecting the GitHub
  repository when creating the protected Release.
- Supersede the unpublished `v0.2.0` tag; Marketplace distribution begins at
  `v0.2.1`.

## 0.2.0 - 2026-10-25 (not published)

- Add one reproducible Jianwei bundle for Claude Code, Codex, and DSH.
- Add guided setup, Tanyuan 0.5.0 data access, research, HTML report, and
  research-then-report skills.
- Add isolated runtime setup with pinned dependencies and explicit credential
  handling.
- Add deterministic offline QA, authenticated release artifacts, immutable
  source releases, and protected Marketplace delivery.

## 0.1.1 - 2026-10-25

- Add DSH Marketplace compatibility metadata and installation guidance.

## 0.1.0 - 2026-10-25

- Publish the initial Tanyuan and research skill bundle.
