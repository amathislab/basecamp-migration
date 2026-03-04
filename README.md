# Basecamp 4 Account Migration

Migrate projects between Basecamp 4 accounts via the REST API, preserving authorship, comments, boosts, and file attachments.

## Setup

```bash
uv sync
cp .env.example .env  # Fill in your OAuth credentials and account IDs
```

## Authentication

```bash
# Initial OAuth flow (opens browser)
uv run python auth.py

# Add a remote team member's token (they paste a code)
uv run python auth.py add-remote

# Refresh an expired access token
uv run python auth.py refresh
```

## Migration

```bash
# List all source projects
uv run python migrate.py

# Migrate a single project
uv run python migrate.py <source_project_id>
```

### What gets migrated

| Content | Authorship | Original date |
|---------|-----------|---------------|
| Messages | Token routing | In content body |
| Documents | Token routing | In content body |
| To-dos | Token routing | In description |
| File uploads | Token routing | In description |
| Schedule entries | Token routing | In description |
| Comments | Token routing | In content body |
| Chat/Campfire | Token routing | Inline prefix |
| Boosts (emoji) | Token routing | N/A |

**Token routing**: If we have the original author's OAuth token, content is posted as them. Otherwise, it's posted via fallback (your token) with an attribution note.

### Resume & idempotency

Migration state is persisted to `id_map.json`. If interrupted, re-run the same command to resume from where it left off. Items already migrated (tracked by source ID) are skipped.

### Fallback tracking

Items created without the correct author's token are tracked in `id_map.json` under `_fallback`. Once you collect the missing token, these can be re-migrated with correct authorship.

## Files

| File | Purpose |
|------|---------|
| `env.py` | Loads `.env` config |
| `auth.py` | OAuth flows + token management |
| `api_client.py` | Rate-limited Basecamp API client |
| `id_mapper.py` | Persistent source-to-dest ID mapping |
| `migrate.py` | Migration orchestrator |
| `get_my_token.py` | Standalone script for remote token collection |
