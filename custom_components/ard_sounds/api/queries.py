"""Named ARD GraphQL operations, verified on 2026-10-04."""

IMAGE = "image { url url1X1 }"
STATION = "publicationService { id title organizationName }"
SHOW = f"id coreId title synopsis {IMAGE} {STATION}"
EPISODE_FILTER = """
filter: { isPublished: { equalTo: true },
          itemType: { notEqualTo: EVENT_LIVESTREAM } }
"""
SHOW_WITH_EPISODES = f"""
{SHOW}
availableEpisodes: items(first: 1, {EPISODE_FILTER}) {{ nodes {{ id }} }}
"""
AUDIO = """
audios { url mimeType }
audioList { href distributionType audioCodec availableFrom availableTo }
"""
STREAM = f"id coreId title {IMAGE} {STATION} {AUDIO}"
EPISODE = f"id coreId title synopsis duration publishDate isPublished {IMAGE} {AUDIO}"
PAGE = "totalCount pageInfo { hasNextPage endCursor }"

QUERIES = {
    "Stations": f"""
        query Stations($first: Int!, $after: Cursor) {{
          permanentLivestreams(
            first: $first, after: $after, orderBy: PRIMARY_KEY_ASC
          ) {{
            {PAGE} nodes {{ {STREAM} }}
          }}
        }}""",
    "Shows": f"""
        query Shows($first: Int!, $after: Cursor) {{
          programSets(first: $first, after: $after, orderBy: PRIMARY_KEY_ASC) {{
            {PAGE} nodes {{ {SHOW_WITH_EPISODES} }}
          }}
        }}""",
    "Search": f"""
        query Search($query: String!, $limit: Int!, $offset: Int!) {{
          search(query: $query, type: ProgramSets, limit: $limit, offset: $offset) {{
            programSets {{ {PAGE} nodes {{ {SHOW_WITH_EPISODES} }} }}
          }}
        }}""",
    "Episodes": f"""
        query Episodes($id: ID!, $first: Int!, $after: Cursor) {{
          show(id: $id) {{
            {SHOW}
            items(first: $first, after: $after, orderBy: PUBLISH_DATE_DESC,
                  {EPISODE_FILTER}) {{
              {PAGE} nodes {{ {EPISODE} }}
            }}
          }}
        }}""",
    "Show": f"query Show($id: ID!) {{ show(id: $id) {{ {SHOW_WITH_EPISODES} }} }}",
    "Episode": f"query Episode($id: ID!) {{ item(id: $id) {{ {EPISODE} }} }}",
    "Stream": f"""
        query Stream($id: String!) {{ permanentLivestream(id: $id) {{ {STREAM} }} }}
        """,
}
