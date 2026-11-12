# Changelog

## 0.6.0 - 2026-11-13

- Add opt-in `ty call --view brief` for news feeds, flashes, and web search;
  preserve provenance, dates, coverage and continuation while declaring display
  omissions. The default full response and query semantics remain unchanged.
- Clarify RSS time filters and unify incomplete-result recovery: configured
  DeepSeek/Tavily search can supplement direct sources, with explicit provider
  choice and proportionate stopping when reliable evidence remains unavailable.
- Support open-ended opportunity discovery within research and replace fixed
  tool sequences with optional examples, keeping interface definitions in CLI help.
- Bundle Tanyuan 0.7.0. Installation, shared runtime and workspace layout are
  unchanged; no migration is required.

## 0.5.1 - 2026-11-12

- Clarify that historical archives preserve context and evidence, not current user
  decisions or authorization; reassess earlier proposals against current information.
- Preserve the purpose, evidence, open questions, and decision boundaries when
  handing work to a later task without prescribing its future conclusion.
- Add paired manual acceptance examples for archives and reminders. Installation,
  shared runtime, and bundled Tanyuan 0.6.0 are unchanged; no migration is required.

## 0.5.0 - 2026-11-12

- Add `ty capabilities` for credential checks and token-aware capability probes; it
  never prints secrets and skips quota-sensitive `report_rc` by default.
- Paginate `filing.announcements` (`page/has_more/next_page`) and document
  `first_ann_date`/`update_flag` for earnings disclosures.
- Add `fundamental.consensus` (aggregated sell-side forecasts) and
  `events.calendar` (disclosure, unlock and dividend windows) with explicit
  scheduled/actual status, units, warnings and partial-failure reporting.
- Map Tushare rate-limit errors to `RATE_LIMITED` instead of `BAD_ARGUMENT`.
- Bundle Tanyuan 0.6.0; existing jianwei skills and installation layout are
  unchanged. No migration is required.
- Rebuild `plugin/` and add offline regression coverage; build and Marketplace
  contracts pass.

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
