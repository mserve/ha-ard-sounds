# ARD API contract fixtures

Recorded on **2026-10-04** from `https://api.ardaudiothek.de/graphql`, using POST
with `operationName`, `query`, and `variables`. The executable query documents are
in `custom_components/ard_sounds/api/queries.py`. Introspection is not performed
at runtime.

| Files | Operation | Variables | Response |
| --- | --- | --- | --- |
| `stations.json`, `stations_next.json` | Stations | `first: 2`, `after: null` / first response's cursor | `data.permanentLivestreams` |
| `shows.json`, `shows_next.json` | Shows | `first: 2`, `after: null` / first response's cursor | `data.programSets` |
| `search0.json`, `search2.json` | Search | `query: "Wissen"`, `limit: 2`, `offset: 0` / `2` | `data.search.programSets` |
| `episodes.json`, `episodes_next.json` | Episodes | `id: "62520168"`, `first: 2`, `after: null` / first response's cursor | `data.show.items` |

Connections contain `nodes`, `pageInfo.hasNextPage`, `pageInfo.endCursor`, and
`totalCount`. Totals vary and do not control termination. Stations and shows use
primary-key ordering; episodes use publication date descending. Continuations
were verified to return different IDs. Search returned `hasNextPage: true` with a
null cursor, so its next offset advances by the returned node count.

`id` is required for an accepted node; `coreId`, synopsis, images, duration, dates,
publication service, and audio arrays can be absent or null. Image URLs contain a
`{width}` placeholder, replaced with 512 for browse thumbnails. Invalid nodes are
skipped, IDs are deduplicated within pages, and titles never identify content.
Empty pages terminate regardless of totals; missing or repeated cursors on
nonempty continued catalog pages raise explicit errors. The radio catalog is
bounded to 20 pages of 100; show/episode pages are 10–100. Offset routes are bounded
to 10,000.

Live records are stream variants, grouped by `publicationService.id` and
`organizationName`; multiple variants are preserved. The global `programSets`
catalog is not claimed to be a complete ARD podcast index; public search and
station-specific catalogs can differ. For example, the sampled MDR KULTUR
publication service reported 235 `programSets` versus 35 `shows`; this difference
is recorded as an API completeness limit.

Audio URLs are in `audios.url` and `audioList.href`; protocol-relative URLs use
HTTPS. `audioList` supplies distribution and availability windows. MP3 aliases
normalize to `audio/mpeg`, while HLS remains a playlist. On-demand MP3 is preferred
over download MP3; valid alternate candidates are retained. `feedUrl` is not used
as an RSS or playable audio URL.

Numeric IDs and core URNs were exercised for sampled `show`, `item`, and
`permanentLivestream` lookups. Missing IDs are expected to return null and become
unavailable-content errors. Browse IDs prefer `coreId` where present, with the API
ID as fallback; previously stored numeric identifiers remain accepted. Removed
IDs are not silently redirected to unrelated content.

A bounded GET (read at most 1,024 bytes) to the first live MP3 followed one redirect
and returned `audio/mpeg`; the first episode MP3 returned HTTP 206 and
`audio/mpeg`. A sampled MDR THÜRINGEN HLS URL returned HTTP 200 with
`application/x-mpegURL` and an `#EXTM3U` playlist prefix, without redirects. This verifies server reachability and content headers from the dev
machine, not playback from a user's physical media player. The runtime follows at
most five redirects and closes the response without reading/downloading audio.
