# AGENTS.md
## ARD Sounds – Home Assistant Custom Integration

This document defines rules, expectations, and boundaries for any automated agent
(Codex, AI assistants, code generators) working on this repository.

The goal is to keep the integration **Home Assistant compliant**, **HACS compatible**,
and **maintainable**.

---

## Project Overview

**Name:** ARD Sounds
**Domain:** `ard_sounds`
**Type:** Home Assistant Custom Integration
**Distribution:** HACS
**Config type:** UI Config Flow
**Primary features:**
- Access to ARD Sounds content via public GraphQL API
- Media Browser integration via Media Source
- Playback via Media Players
- Played/unplayed persistence (additional feature)
- Mark podcasts for notifications (additional feature)
- Manual reload service

---

## Architecture Principles

### Home Assistant First
- Follow Home Assistant core patterns and best practices.
- Prefer `DataUpdateCoordinator` for polling and refresh logic, if required for any periodically occuring background tasks.
- Use `Media Source` for media browsing (not MediaPlayer browse hooks).
- Never block the event loop (all I/O must be async).

### Scope Discipline
This project intentionally does **not** include:
- Authentication and personalized content
- Offline downloads or caching of audio
- UI dashboards

Agents must **not introduce these features** unless explicitly requested.

---

## Directory & File Structure

Agents must respect and preserve this structure:

custom_components/ard_sounds/
├─ api/
|   └─ api_client.py
├─ translations/
|   ├─ de.json
|   └─ en.json
├─ __init__.py
├─ classes.py
├─ config_flow.py
├─ const.py
├─ manifest.json
├─ media_source.py
└─ models.py

Tests shall follow pytest typical naming and must be in directory `tests/`.

Additional files:
- `hacs.json` (repo root)
- `.ruff.toml` (ruff config)
- `.github/workflows/*`
- `scripts/develop`
- `scripts/lint`
- `scripts/setup`

Do **not** move runtime files outside `custom_components/ard_sounds`.

---

## Coding Standards

### Python
- Target Python **3.14** (minimum **3.14.2** for Home Assistant Core 2026.9.4)
- Use type hints where reasonable
- Prefer `TypedDict` / `dataclass` for structured data
- Keep functions small and single-purpose

### Async Rules
- All network access must use Home Assistant's `aiohttp` session
- No synchronous HTTP, file, or sleep calls
- Timeouts and error handling are mandatory for external calls to the API

---

## Linting & Formatting (MANDATORY)

This project uses **Ruff** for linting and formatting.

Agents must:
- Ensure `ruff check .` passes
- Ensure `ruff format .` produces no diff
- Never introduce code that requires disabling Ruff rules globally

Configuration lives in `.ruff.toml`.

---

## Home Assistant Compliance

Agents must ensure:
- `manifest.json` contains all required fields
- `DOMAIN` is always `"ard_sounds"`
- Logging uses `LOGGER`, imported from `.const`
- No hardcoded file paths
- No direct access to HA internals outside public APIs

---

## Media Source Rules
- Do not use deprecated media player constants. Use the new MediaClass, MediaType, and RepeatMode enum instead.
- All media browsing must go through the Media Source platform
- Home Assistant-facing `media_content_id` values must use
  `media-source://ard_sounds/<identifier>`.
- Construct these IDs with `BrowseMediaSource` or `generate_media_source_id`.
- The `ard_sounds://` prefix is an internal identifier convention; map it to
  Home Assistant's Media Source scheme at the platform boundary.

- `async_resolve_media()` must return a **final, playable URL**
- Redirects must be handled
- Content type should be audio (e.g. `audio/mpeg`)

Agents must **not** introduce MediaPlayerEntity subclasses.

---

## Sensors

- No sensors are offered within first state.

---

## Services

Required service:
- `ard_sounds.reload_sources`: enforces reload of station and podcast entries

Service handlers:
- Must be async
- Register services once in `async_setup`, and validate the loaded config entry
  when a service is called
- Must not allow raw API exceptions to escape
- Report expected failures through Home Assistant-handled `HomeAssistantError`
  or `ServiceValidationError`, so callers can detect failure
- Must log failures but keep the integration running

---

## Configuration Rules

- Configuration is **Config Flow** only
- Config Flow validates user input before creating a config entry
- `async_setup_entry` reads stored entry data/options and initializes runtime
  objects
- `async_setup` handles integration-wide setup and service registration
- Store entry-owned runtime objects in typed `ConfigEntry.runtime_data`
- Invalid feeds must not crash setup
- Missing optional fields must have safe defaults

---

## Translation rules
- Use hassfest compliant translations
- Always update German and English translations


## Testing Expectations

Agents should add or update tests when:
- Core logic changes
- Data structures change
- Public behavior changes

Minimum expectations:
- `pytest -q` runs without error
- Coordinator logic is testable without real HTTP calls

---

## Validation & CI

Agents must keep CI green:
- hassfest
- HACS validation
- Ruff (lint + format)
- pytest

Agents must **not** disable or weaken validation workflows.

---

## Commit & Change Discipline

When acting as a coding agent:
- Make changes in logical, reviewable chunks
- Do not mix refactors with new features
- Do not rename public entities or services without instruction

---

## Golden Rule

If unsure:
- Prefer **Home Assistant conventions**
- Prefer **simpler solutions**
- Prefer **explicit behavior over clever abstractions**

When in doubt, ask before expanding scope.

---
