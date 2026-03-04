"""
Basecamp 4 migration: reads from source account, writes to destination account.

Usage:
  python migrate.py <source_project_id>
"""

import sys
import json
from api_client import BasecampClient
from id_mapper import IDMapper
from env import SOURCE_ACCOUNT, DEST_ACCOUNT


def load_config():
    with open("config.json") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Token router: picks the right client per original creator
# ---------------------------------------------------------------------------

class TokenRouter:
    """Routes API writes through the correct user's token."""

    def __init__(self, account_id: int, config: dict, src_people: list[dict]):
        self.account_id = account_id
        self.default = BasecampClient(account_id)  # fallback (your token)
        self._clients: dict[int, BasecampClient] = {}  # src_user_id -> client

        user_tokens = config.get("user_tokens", {})
        # Build src_user_id -> email mapping
        for p in src_people:
            email = (p.get("email_address") or "").lower()
            token_entry = user_tokens.get(email)
            if token_entry:
                self._clients[p["id"]] = BasecampClient(
                    account_id, access_token=token_entry["access_token"]
                )
                print(f"  Token: {p['name']} ({email}) -> has own token")
            else:
                print(f"  Token: {p['name']} ({email}) -> will use fallback")

    def for_creator(self, item: dict) -> BasecampClient:
        """Get the client matching the original creator of an item."""
        # Boosts use "booster" instead of "creator"
        creator_id = (item.get("creator") or item.get("booster", {})).get("id")
        return self._clients.get(creator_id, self.default)


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def attribution(item: dict) -> str:
    """Generate an attribution prefix preserving original author + date."""
    author = item.get("creator", {}).get("name", "Unknown")
    date = item.get("created_at", "")[:10]
    return f'<p><em>Originally by {author} on {date}</em></p>'


def with_date_note(content: str | None, item: dict) -> str:
    """Prepend original date (author is handled by token routing)."""
    date = item.get("created_at", "")[:10]
    prefix = f'<p><em>Originally on {date}</em></p>'
    return prefix + (content or "")


def with_attribution(content: str | None, item: dict, has_token: bool) -> str:
    """If we have the user's token, only add date. Otherwise add full attribution."""
    if has_token:
        return with_date_note(content, item)
    return attribution(item) + (content or "")


def map_user_ids(src_ids: list[int], people_map: dict[int, int]) -> list[int]:
    """Map source user IDs to destination user IDs, skipping unmapped."""
    return [people_map[uid] for uid in src_ids if uid in people_map]


# ---------------------------------------------------------------------------
# People mapping
# ---------------------------------------------------------------------------

def build_people_map(src: BasecampClient, dst: BasecampClient, src_project_id: int) -> dict[int, int]:
    """Map source→dest user IDs by email address."""
    src_people = src.get_json(f"/projects/{src_project_id}/people.json")
    dst_people = dst.get_all("/people.json")

    dst_by_email = {(p.get("email_address") or "").lower(): p["id"] for p in dst_people if p.get("email_address")}

    mapping = {}
    print("\nPeople mapping:")
    for p in src_people:
        email = (p.get("email_address") or "").lower()
        dst_id = dst_by_email.get(email)
        if dst_id:
            mapping[p["id"]] = dst_id
            print(f"  {p['name']} ({email}) -> dest ID {dst_id}")
        else:
            print(f"  {p['name']} ({email}) -> NOT FOUND in destination")

    return mapping


def grant_project_access(dst: BasecampClient, dest_project_id: int,
                         people_map: dict[int, int]):
    """Grant all mapped destination users access to the project."""
    dest_ids = list(set(people_map.values()))
    if not dest_ids:
        return
    print(f"\nGranting project access to {len(dest_ids)} users...")
    resp = dst.put(f"/projects/{dest_project_id}/people/users.json",
                   data={"grant": dest_ids})
    resp.raise_for_status()
    print("  Access granted.")


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

def get_dock(project: dict) -> dict[str, int]:
    """Extract dock tool name→id mapping from a project response."""
    return {tool["name"]: tool["id"] for tool in project.get("dock", [])}


def migrate_project(src: BasecampClient, dst: BasecampClient, mapper: IDMapper,
                     src_project_id: int) -> tuple[dict, dict]:
    """Create the project on destination. Returns (dest_project, dest_dock)."""
    if mapper.has("project", src_project_id):
        dest_id = mapper.get("project", src_project_id)
        print(f"Project already migrated (dest ID: {dest_id}), fetching...")
        dest_project = dst.get_json(f"/projects/{dest_id}.json")
        return dest_project, get_dock(dest_project)

    src_project = src.get_json(f"/projects/{src_project_id}.json")

    dest_project = dst.post_json("/projects.json", {
        "name": src_project["name"],
        "description": src_project.get("description", ""),
    })

    mapper.set("project", src_project_id, dest_project["id"])
    print(f"Created project '{dest_project['name']}' (dest ID: {dest_project['id']})")
    return dest_project, get_dock(dest_project)


# ---------------------------------------------------------------------------
# To-do lists & to-dos
# ---------------------------------------------------------------------------

def migrate_todos(src: BasecampClient, router: TokenRouter, mapper: IDMapper,
                  src_project_id: int, dest_project_id: int,
                  src_todoset_id: int, dest_todoset_id: int,
                  people_map: dict[int, int]):
    """Migrate all to-do lists and their items."""
    todolists = src.get_all(f"/buckets/{src_project_id}/todosets/{src_todoset_id}/todolists.json")
    print(f"\nMigrating {len(todolists)} to-do lists...")

    for tl in todolists:
        _migrate_todolist(src, router, mapper, src_project_id, dest_project_id,
                          dest_todoset_id, tl, people_map)


def _migrate_todolist(src, router, mapper, src_pid, dest_pid, dest_todoset_id, tl, people_map):
    if mapper.has("todolist", tl["id"]):
        print(f"  Todolist '{tl['name']}' already migrated, skipping")
        dest_tl_id = mapper.get("todolist", tl["id"])
    else:
        dst = router.for_creator(tl)
        dest_tl = dst.post_json(
            f"/buckets/{dest_pid}/todosets/{dest_todoset_id}/todolists.json",
            {"name": tl["name"], "description": tl.get("description", "")},
        )
        dest_tl_id = dest_tl["id"]
        mapper.set("todolist", tl["id"], dest_tl_id,
                   fallback=(dst is router.default))
        print(f"  Created todolist '{tl['name']}'")

    # Migrate active + completed todos
    for status_param in ["", "completed=true"]:
        url = f"/buckets/{src_pid}/todolists/{tl['id']}/todos.json"
        if status_param:
            url += f"?{status_param}"
        todos = src.get_all(url)

        for todo in todos:
            _migrate_todo(src, router, mapper, src_pid, dest_pid, dest_tl_id, todo, people_map,
                          is_completed=bool(status_param))


def _migrate_todo(src, router, mapper, src_pid, dest_pid, dest_tl_id, todo, people_map, is_completed):
    if mapper.has("todo", todo["id"]):
        return

    dst = router.for_creator(todo)
    has_token = dst is not router.default

    assignee_ids = [a["id"] for a in todo.get("assignees", [])]
    dest_assignees = map_user_ids(assignee_ids, people_map)

    content_body = with_attribution(todo.get("description", ""), todo, has_token)

    dest_todo = dst.post_json(
        f"/buckets/{dest_pid}/todolists/{dest_tl_id}/todos.json",
        {
            "content": todo["content"],
            "description": content_body,
            "assignee_ids": dest_assignees,
            "due_on": todo.get("due_on"),
            "starts_on": todo.get("starts_on"),
            "notify": False,
        },
    )
    mapper.set("todo", todo["id"], dest_todo["id"], fallback=not has_token)

    if is_completed or todo.get("completed"):
        dst.post(f"/buckets/{dest_pid}/todos/{dest_todo['id']}/completion.json")

    creator = todo.get("creator", {}).get("name", "?")
    token_tag = "own" if has_token else "fallback"
    print(f"    Todo: '{todo['content'][:50]}' [{token_tag}:{creator}] {'[done]' if is_completed else ''}")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def migrate_messages(src: BasecampClient, router: TokenRouter, mapper: IDMapper,
                     src_project_id: int, dest_project_id: int,
                     src_board_id: int, dest_board_id: int):
    """Migrate all messages."""
    messages = src.get_all(f"/buckets/{src_project_id}/message_boards/{src_board_id}/messages.json")
    print(f"\nMigrating {len(messages)} messages...")

    messages.sort(key=lambda m: m["created_at"])

    for msg in messages:
        if mapper.has("message", msg["id"]):
            print(f"  Message '{msg['subject'][:40]}' already migrated, skipping")
            continue

        full_msg = src.get_json(f"/buckets/{src_project_id}/messages/{msg['id']}.json")
        dst = router.for_creator(full_msg)
        has_token = dst is not router.default
        content = with_attribution(full_msg.get("content", ""), full_msg, has_token)

        dest_msg = dst.post_json(
            f"/buckets/{dest_project_id}/message_boards/{dest_board_id}/messages.json",
            {
                "subject": full_msg["subject"],
                "content": content,
                "status": "active",
            },
        )
        mapper.set("message", msg["id"], dest_msg["id"], fallback=not has_token)
        creator = full_msg.get("creator", {}).get("name", "?")
        tag = "own" if has_token else "fallback"
        print(f"  Message: '{msg['subject'][:50]}' [{tag}:{creator}]")


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def migrate_documents(src: BasecampClient, router: TokenRouter, mapper: IDMapper,
                      src_project_id: int, dest_project_id: int,
                      src_vault_id: int, dest_vault_id: int):
    """Migrate documents from Docs & Files."""
    docs = src.get_all(f"/buckets/{src_project_id}/vaults/{src_vault_id}/documents.json")
    print(f"\nMigrating {len(docs)} documents...")

    for doc in docs:
        if mapper.has("document", doc["id"]):
            print(f"  Doc '{doc['title'][:40]}' already migrated, skipping")
            continue

        full_doc = src.get_json(f"/buckets/{src_project_id}/documents/{doc['id']}.json")
        dst = router.for_creator(full_doc)
        has_token = dst is not router.default
        content = with_attribution(full_doc.get("content", ""), full_doc, has_token)

        dest_doc = dst.post_json(
            f"/buckets/{dest_project_id}/vaults/{dest_vault_id}/documents.json",
            {
                "title": full_doc["title"],
                "content": content,
                "status": "active",
            },
        )
        mapper.set("document", doc["id"], dest_doc["id"], fallback=not has_token)
        print(f"  Doc: '{doc['title']}'")


# ---------------------------------------------------------------------------
# File uploads
# ---------------------------------------------------------------------------

def migrate_uploads(src: BasecampClient, router: TokenRouter, mapper: IDMapper,
                    src_project_id: int, dest_project_id: int,
                    src_vault_id: int, dest_vault_id: int):
    """Migrate uploaded files from Docs & Files."""
    uploads = src.get_all(f"/buckets/{src_project_id}/vaults/{src_vault_id}/uploads.json")
    print(f"\nMigrating {len(uploads)} uploads...")

    for upload in uploads:
        if mapper.has("upload", upload["id"]):
            print(f"  Upload '{upload['title'][:40]}' already migrated, skipping")
            continue

        full_upload = src.get_json(f"/buckets/{src_project_id}/uploads/{upload['id']}.json")
        download_url = full_upload.get("download_url")
        if not download_url:
            print(f"  Upload '{upload['title']}' has no download URL, skipping")
            continue

        resp = src.get(download_url)
        resp.raise_for_status()
        file_bytes = resp.content
        content_type = full_upload.get("content_type", "application/octet-stream")
        filename = full_upload.get("filename", upload["title"])

        dst = router.for_creator(full_upload)
        has_token = dst is not router.default
        sgid = dst.upload_file(filename, file_bytes, content_type)
        description = with_attribution(full_upload.get("description", ""), full_upload, has_token)

        dest_upload = dst.post_json(
            f"/buckets/{dest_project_id}/vaults/{dest_vault_id}/uploads.json",
            {"attachable_sgid": sgid, "description": description},
        )
        mapper.set("upload", upload["id"], dest_upload["id"], fallback=not has_token)
        print(f"  Upload: '{upload['title']}'")


# ---------------------------------------------------------------------------
# Schedule entries
# ---------------------------------------------------------------------------

def migrate_schedule(src: BasecampClient, router: TokenRouter, mapper: IDMapper,
                     src_project_id: int, dest_project_id: int,
                     src_schedule_id: int, dest_schedule_id: int,
                     people_map: dict[int, int]):
    """Migrate schedule entries."""
    entries = src.get_all(f"/buckets/{src_project_id}/schedules/{src_schedule_id}/entries.json")
    print(f"\nMigrating {len(entries)} schedule entries...")

    for entry in entries:
        if mapper.has("schedule_entry", entry["id"]):
            print(f"  Entry '{entry['summary'][:40]}' already migrated, skipping")
            continue

        full_entry = src.get_json(f"/buckets/{src_project_id}/schedule_entries/{entry['id']}.json")

        participant_ids = [p["id"] for p in full_entry.get("participants", [])]
        dest_participants = map_user_ids(participant_ids, people_map)

        dst = router.for_creator(full_entry)
        has_token = dst is not router.default
        description = with_attribution(full_entry.get("description", ""), full_entry, has_token)

        dest_entry = dst.post_json(
            f"/buckets/{dest_project_id}/schedules/{dest_schedule_id}/entries.json",
            {
                "summary": full_entry["summary"],
                "starts_at": full_entry["starts_at"],
                "ends_at": full_entry["ends_at"],
                "description": description,
                "participant_ids": dest_participants,
                "all_day": full_entry.get("all_day", False),
                "notify": False,
            },
        )
        mapper.set("schedule_entry", entry["id"], dest_entry["id"], fallback=not has_token)
        print(f"  Entry: '{entry['summary'][:50]}'")


# ---------------------------------------------------------------------------
# Comments (on any recording: message, todo, document, upload, schedule_entry)
# ---------------------------------------------------------------------------

BOOSTABLE_TYPES = ["message", "todo", "document", "upload", "schedule_entry",
                   "comment", "chat_line"]
COMMENTABLE_TYPES = ["message", "todo", "document", "upload", "schedule_entry"]


def migrate_comments(src: BasecampClient, router: TokenRouter, mapper: IDMapper,
                     src_project_id: int, dest_project_id: int):
    """Migrate comments on all migrated recordings."""
    print("\nMigrating comments...")
    total = 0

    for entity_type in COMMENTABLE_TYPES:
        mappings = mapper.get_all(entity_type)
        for src_id_str, dest_id in mappings.items():
            src_id = int(src_id_str)
            comments = src.get_all(
                f"/buckets/{src_project_id}/recordings/{src_id}/comments.json"
            )
            comments.sort(key=lambda c: c["created_at"])

            for comment in comments:
                if mapper.has("comment", comment["id"]):
                    continue

                full_comment = src.get_json(
                    f"/buckets/{src_project_id}/comments/{comment['id']}.json"
                )
                dst = router.for_creator(full_comment)
                has_token = dst is not router.default
                content = with_attribution(full_comment.get("content", ""), full_comment, has_token)

                dest_comment = dst.post_json(
                    f"/buckets/{dest_project_id}/recordings/{dest_id}/comments.json",
                    {"content": content},
                )
                mapper.set("comment", comment["id"], dest_comment["id"],
                           fallback=not has_token)
                total += 1

    print(f"  Migrated {total} comments")


# ---------------------------------------------------------------------------
# Campfire / Chat
# ---------------------------------------------------------------------------

def migrate_campfire(src: BasecampClient, router: TokenRouter, mapper: IDMapper,
                     src_project_id: int, dest_project_id: int,
                     src_chat_id: int, dest_chat_id: int):
    """Migrate campfire/chat lines (plain text only)."""
    lines = src.get_all(f"/buckets/{src_project_id}/chats/{src_chat_id}/lines.json")
    print(f"\nMigrating {len(lines)} chat lines...")

    lines.sort(key=lambda l: l["created_at"])

    for line in lines:
        if mapper.has("chat_line", line["id"]):
            continue

        dst = router.for_creator(line)
        has_token = dst is not router.default
        body = line.get("body", "") or ""

        if has_token:
            # Post as the original user, just add date
            date = line.get("created_at", "")[:16].replace("T", " ")
            text = f"[{date}] {body}"
        else:
            author = line.get("creator", {}).get("name", "Unknown")
            date = line.get("created_at", "")[:16].replace("T", " ")
            text = f"[{author}, {date}] {body}"

        dest_line = dst.post_json(
            f"/buckets/{dest_project_id}/chats/{dest_chat_id}/lines.json",
            {"content": text},
        )
        mapper.set("chat_line", line["id"], dest_line["id"], fallback=not has_token)

    print(f"  Done ({len(lines)} lines)")


# ---------------------------------------------------------------------------
# Boosts (emoji reactions on any recording)
# ---------------------------------------------------------------------------

def migrate_boosts(src: BasecampClient, router: TokenRouter, mapper: IDMapper,
                   src_project_id: int, dest_project_id: int):
    """Migrate boosts (emoji reactions) on all migrated recordings."""
    print("\nMigrating boosts...")
    total = 0

    for entity_type in BOOSTABLE_TYPES:
        mappings = mapper.get_all(entity_type)
        for src_id_str, dest_id in mappings.items():
            src_id = int(src_id_str)
            boosts = src.get_all(
                f"/buckets/{src_project_id}/recordings/{src_id}/boosts.json"
            )
            for boost in boosts:
                if mapper.has("boost", boost["id"]):
                    continue

                dst = router.for_creator(boost)
                dest_boost = dst.post_json(
                    f"/buckets/{dest_project_id}/recordings/{dest_id}/boosts.json",
                    {"content": boost["content"]},
                )
                has_token = dst is not router.default
                mapper.set("boost", boost["id"], dest_boost["id"],
                           fallback=not has_token)
                total += 1

    print(f"  Migrated {total} boosts")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def migrate_project_full(src_project_id: int):
    """Run the full migration pipeline for a single project."""
    config = load_config()
    src = BasecampClient(SOURCE_ACCOUNT)
    dst = BasecampClient(DEST_ACCOUNT)
    mapper = IDMapper()

    # 1. Create project
    print("=" * 60)
    print(f"Migrating project {src_project_id}")
    print("=" * 60)

    dest_project, dest_dock = migrate_project(src, dst, mapper, src_project_id)
    dest_pid = dest_project["id"]

    # Get source dock + people
    src_project = src.get_json(f"/projects/{src_project_id}.json")
    src_dock = get_dock(src_project)
    src_people = src.get_json(f"/projects/{src_project_id}/people.json")

    # 2. Map people, grant access, build token router
    people_map = build_people_map(src, dst, src_project_id)
    grant_project_access(dst, dest_pid, people_map)
    print("\nToken routing:")
    router = TokenRouter(DEST_ACCOUNT, config, src_people)

    # 3. Documents & uploads
    if "vault" in src_dock and "vault" in dest_dock:
        migrate_documents(src, router, mapper, src_project_id, dest_pid,
                          src_dock["vault"], dest_dock["vault"])
        migrate_uploads(src, router, mapper, src_project_id, dest_pid,
                        src_dock["vault"], dest_dock["vault"])

    # 4. To-dos
    if "todoset" in src_dock and "todoset" in dest_dock:
        migrate_todos(src, router, mapper, src_project_id, dest_pid,
                      src_dock["todoset"], dest_dock["todoset"], people_map)

    # 5. Messages
    if "message_board" in src_dock and "message_board" in dest_dock:
        migrate_messages(src, router, mapper, src_project_id, dest_pid,
                         src_dock["message_board"], dest_dock["message_board"])

    # 6. Schedule
    if "schedule" in src_dock and "schedule" in dest_dock:
        migrate_schedule(src, router, mapper, src_project_id, dest_pid,
                         src_dock["schedule"], dest_dock["schedule"], people_map)

    # 7. Comments (on all migrated items)
    migrate_comments(src, router, mapper, src_project_id, dest_pid)

    # 8. Campfire
    if "chat" in src_dock and "chat" in dest_dock:
        migrate_campfire(src, router, mapper, src_project_id, dest_pid,
                         src_dock["chat"], dest_dock["chat"])

    # 9. Boosts (emoji reactions on all migrated items)
    migrate_boosts(src, router, mapper, src_project_id, dest_pid)

    print("\n" + "=" * 60)
    print("Migration complete!")
    mapper.summary()
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python migrate.py <source_project_id>")
        print("\nSource projects:")
        src = BasecampClient(SOURCE_ACCOUNT)
        for p in src.get_all("/projects.json"):
            print(f"  {p['id']}  {p['name']}")
        sys.exit(1)

    migrate_project_full(int(sys.argv[1]))
