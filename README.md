# ARD Sounds

A Home Assistant custom integration for browsing public ARD radio streams and
podcasts in the Media Browser. No ARD account is required.

Requires **Home Assistant 2026.9.4 or newer**. Development requires **Python
3.14.2 or newer**.

## Installation and setup

Add `https://github.com/mserve/ha-ard-sounds` to HACS as a custom **Integration**
repository and download ARD Sounds. Alternatively, copy
`custom_components/ard_sounds` into your Home Assistant configuration's
`custom_components` directory. Restart Home Assistant, then open
**Settings → Devices & services → Add integration → ARD Sounds** and confirm
setup. The integration checks API access before creating its single config entry.
Configuration uses the UI; no YAML entry is needed.

## Browsing and playing

Open **Media → ARD Sounds**. **Live radio** groups streams by broadcaster and
station, retaining regional stream variants. **Podcasts** opens A–Z and **#**
folders. Each letter contains all matching shows in alphabetical order, ignoring
case and accents (Ä under A, Ö under O, Ü under U). Numbers, symbols, and other
scripts are grouped under **#**. Open a show to browse its published, currently
available audio episodes.
Podcasts with no published episodes are hidden from letter folders and search.
Use search from ARD Sounds or Podcasts to find shows by name. **Next page** folders
provide more search results or older episodes. Letter folders do not require
flipping through catalog pages.

Select a media player and choose a station stream or episode. Home Assistant
receives a `media-source://ard_sounds/...` ID. The integration refreshes audio
metadata, follows redirects, and returns a final playable URL. It prefers MP3 and
on-demand distributions and retains HLS playlists as an HLS fallback. Support for
HLS and other audio formats depends on the selected player. Playback requires
that the player itself can reach the public audio host; the integration does not
proxy or download audio.

In **Settings → Devices & services → ARD Sounds → Configure**, adjust search
results and episodes per page (10–100 each). Defaults are 50 results and 30
episodes. Changing these preferences reloads the entry.

## Refreshing the catalog

Run the **ARD Sounds: Reload sources** action (`ard_sounds.reload_sources`) from
Developer Tools or an automation:

```yaml
action: ard_sounds.reload_sources
data: {}
```

This invalidates in-memory metadata and refreshes previously browsed pages, with
at most four concurrent requests. For an unbrowsed catalog it fetches only the
first station and podcast pages. An unavailable integration or a partial refresh
failure reports an action error. Station and show metadata expires after an hour,
episode lists after 15 minutes, and searches after five minutes. Errors are never
served as a successful empty catalog, and expired data is refreshed on demand.

## Limits and planned features

The ARD public API can change or remove content. Catalog pages reflect the API's
ordering and are not an assurance that every ARD show is discoverable. Radio
listing is bounded to 20 pages of 100 records. Alphabetical browsing follows up to
100 catalog pages of 100 shows internally, sharing cached metadata between
letters. The first letter may take longer to open while metadata is loaded.
Checking episode presence can take about a minute on a cold catalog load;
subsequent letters reuse metadata cached for one hour.
Catalogs exceeding these bounds report an error rather than showing a partial
list. Search uses offsets because ARD currently returns null cursors
for search results. A player may require a different supported audio format, and
redirected live URLs can expire.

The first release is entity-free, with no sensors or background polling. Podcast
starring, episode notifications, later-stage sensors, and played/unplayed
persistence remain planned in T08 of [TASKS.md](TASKS.md). ARD authentication,
personalized content, offline downloads, and dashboards are outside the scope.

## Development and validation

`scripts/setup` installs dependencies; `scripts/develop` starts the development
Home Assistant on port 8123. `PyTurboJPEG` and the native `libturbojpeg` library
support Home Assistant's own camera/media dependencies; they are not ARD runtime
requirements. The devcontainer supplies the native library.

```sh
ruff check .
ruff format --check .
pytest -q
```

Tests use recorded metadata and mocked HTTP calls, without contacting ARD. CI also
runs Home Assistant hassfest and HACS validation. See
[the API fixture contract](tests/fixtures/README.md) for query variables and
verified API behavior.
