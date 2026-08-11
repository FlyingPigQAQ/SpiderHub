# Task 3 Report: Wire CDP auto-launch into L3 fetcher path

**Branch:** `feature/cdp-auto-launch`  
**Commit:** `845fc81` — feat: wire CDP auto-launch into L3 fetcher path  
**Date:** 2026-08-11

## Scope

Connected `ChromeCdpLauncher` to `PlaywrightFetcher`, forced Playwright for both
explicit and enabled CDP modes, and made `AutoFetcher` use `cdp_mode_active()` for
CDP-specific HTTP fallback and ConnectError upgrade behavior.

## Files Changed

| File | Change |
|------|--------|
| `src/spiderhub/downloaders/playwright_fetcher.py` | Ensure CDP endpoint, connect through Playwright, and shut down launcher on exit |
| `src/spiderhub/downloaders/browser_factory.py` | Force Playwright whenever `cdp_mode_active()` is true |
| `src/spiderhub/downloaders/auto_fetcher.py` | Use `cdp_mode_active()` for CDP HTTP preference and upgrade paths |
| `tests/unit/test_playwright_fetcher.py` | Verify launcher/connect usage, no local Playwright launch, and shutdown |
| `tests/unit/test_browser_factory.py` | Verify enabled-without-URL forces Playwright; update fake settings |
| `tests/unit/test_auto_fetcher.py` | Verify enabled-without-URL activates CDP HTTP preference |

## Implementation Summary

- `PlaywrightFetcher` owns an optional `ChromeCdpLauncher`.
- In CDP mode, `__aenter__` starts Playwright, calls
  `ChromeCdpLauncher.ensure_ready(settings)`, stores the returned endpoint, and
  calls `_connect_cdp()`.
- Persistent and ephemeral Playwright launch paths are skipped in CDP mode.
- `__aexit__` disconnects the browser stack, stops Playwright, then calls
  `ChromeCdpLauncher.shutdown()`.
- `_interactive`, `_keep_cdp`, L3 factory selection, and AutoFetcher CDP behavior
  now consistently use `cdp_mode_active(settings)`.
- Existing explicit `browser_cdp_url` behavior remains supported.

## TDD Evidence

### RED

```bash
uv run pytest \
  tests/unit/test_browser_factory.py::test_cdp_enabled_forces_playwright_without_url \
  tests/unit/test_auto_fetcher.py::test_auto_fetcher_cdp_enabled_prefers_l2_flag \
  tests/unit/test_playwright_fetcher.py::test_playwright_fetcher_cdp_enabled_uses_launcher_not_launch \
  -v
```

Result: `3 failed` for the expected missing wiring:

- factory returned `CamoufoxFetcher`
- AutoFetcher CDP preference was `False`
- `PlaywrightFetcher` did not expose `ChromeCdpLauncher`

### GREEN

The same focused command passed: `3 passed in 0.74s`.

Related suite:

```bash
uv run pytest \
  tests/unit/test_browser_factory.py \
  tests/unit/test_auto_fetcher.py \
  tests/unit/test_playwright_fetcher.py \
  tests/unit/test_cdp_launcher.py \
  tests/unit/test_settings.py -v
```

Result: `54 passed in 0.52s`.

### Full verification before commit

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

Results:

- pytest: `120 passed in 0.59s`
- ruff: `All checks passed!`
- mypy: `Success: no issues found in 40 source files`

## Self-Review

| Check | Status |
|-------|--------|
| Enabled CDP without URL forces Playwright | OK (tested) |
| Launcher `ensure_ready` endpoint feeds `_connect_cdp` | OK (tested) |
| Persistent/ephemeral launch paths remain unused in CDP mode | OK (tested) |
| Launcher shutdown runs on normal context exit | OK (tested) |
| AutoFetcher CDP preference covers enabled flag and explicit URL | OK |
| ConnectError upgrade uses the same CDP predicate through `_prefer_http_after_browser` | OK |
| Unknown-engine fake includes `browser_cdp_enabled` | OK |
| No unrelated source/test files changed | OK |

Defect-first review of commit `845fc81` found no actionable regressions.

## Concerns

No blocking concerns. Browser process startup and a live CDP handshake remain
covered through launcher and Playwright mocks rather than a real-browser
integration test, keeping the unit suite deterministic.

## Important Review Fix

- Wrapped CDP setup in `PlaywrightFetcher.__aenter__` so failures after launcher
  creation clean up any partial browser stack, shut down the launcher, stop
  Playwright, clear owned references, and re-raise the original error.
- Added a regression test for successful `ensure_ready()` followed by failed
  `_connect_cdp()`. The test verifies launcher shutdown, Playwright stop, and
  cleared launcher/driver references.

### Fix verification

RED: the focused regression test failed because launcher `shutdown()` had not
been awaited.

```bash
uv run pytest \
  tests/unit/test_playwright_fetcher.py \
  tests/unit/test_browser_factory.py \
  tests/unit/test_auto_fetcher.py -v
```

Result: `37 passed in 0.48s`.
