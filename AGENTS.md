# AGENTS.md
## ARD Sounds – Home Assistant Custom Integration

This document defines rules, expectations, and boundaries for any automated agent
(Codex, AI assistants, code generators) working on this repository.

The goal is to keep the integration **Home Assistant compliant**, **HACS compatible**,
and **maintainable**.

---

## Project Overview

**Name:** ARD Sounds
**Domain:** `ard_doungs`
**Type:** Home Assistant Custom Integration
**Distribution:** HACS
**Config type:** YAML (`configuration.yaml`)
**Primary features:**
- Access to ARD Sounds content via public API
- Media Browser integration via Media Source
- Playback via Media Players
- Played/unplayed persistence
- Manual reload service

---

## Architecture Principles

### Home Assistant First
- Follow Home Assistant core patterns and best practices.
- Prefer `DataUpdateCoordinator` for polling and refresh logic.
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
├─ __init__.py
├─ manifest.json
├─ const.py
├─ coordinator.py
├─ entity.py
├─ media_source.py
├─ ard_sounds.py
└─ translations/en.json


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
- Target Python **3.13*
- Use type hints where reasonable
- Prefer `TypedDict` / `dataclass` for structured data
- Keep functions small and single-purpose

### Async Rules
- All network access must use Home Assistant's `aiohttp` session
- No synchronous HTTP, file, or sleep calls
- Timeouts and error handling are mandatory for external feeds

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
- `DOMAIN` is always `"podcast_hub"`
- Logging uses `LOGGER`, imported from `.const`
- No hardcoded file paths
- No direct access to HA internals outside public APIs

---

## Media Source Rules
- Do not use deprecated media player constants. Use the new MediaClass, MediaType, and RepeatMode enum instead.
- All media browsing must go through the Media Source platform
- `media_content_id` format is fixed:

podcast_hub://<feed_id>/<episode_guid>


- `async_resolve_media()` must return a **final, playable URL**
- Redirects must be handled
- Content type should be audio (e.g. `audio/mpeg`)

Agents must **not** introduce MediaPlayerEntity subclasses.

---

## Sensors

- One sensor per configured podcast feed
- Entity ID pattern:

sensor.podcast_<feed_id>

- Sensor state must be simple (e.g. episode count)
- Rich data must live in attributes
- Attributes must be size-conscious (respect `max_episodes`)

---

## Services

Required service:
- `podcast_hub.reload_sources`

Service handlers:
- Must be async
- Must never raise uncaught exceptions
- Must log failures but keep the integration running

---

## Configuration Rules

- Configuration is both **YAML** and **Config Flow**
- Parsing happens in `async_setup`
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