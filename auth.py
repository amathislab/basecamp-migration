"""
OAuth 2.0 authentication flow for Basecamp 4.

Usage:
  python auth.py          # Run the OAuth flow (opens browser)
  python auth.py refresh  # Refresh an existing token
"""

import json
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests

from env import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, AUTH_URL, TOKEN_URL, USER_AGENT

CONFIG_FILE = "config.json"


class CallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect and captures the verification code."""

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [None])[0]

        if code:
            self.server.auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authorization successful!</h1><p>You can close this tab.</p>")
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>No code received.</h1>")

    def log_message(self, format, *args):
        pass  # Suppress request logs


def exchange_code(code: str) -> dict:
    """Exchange the authorization code for access + refresh tokens."""
    resp = requests.post(TOKEN_URL, params={
        "type": "web_server",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "client_secret": CLIENT_SECRET,
        "code": code,
    })
    resp.raise_for_status()
    return resp.json()


def refresh_token(token: str) -> dict:
    """Refresh an expired access token."""
    resp = requests.post(TOKEN_URL, params={
        "type": "refresh",
        "refresh_token": token,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    })
    resp.raise_for_status()
    return resp.json()


def get_accounts(access_token: str) -> list[dict]:
    """Fetch authorized accounts, filtering for Basecamp 4 (bc3)."""
    resp = requests.get(
        "https://launchpad.37signals.com/authorization.json",
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": USER_AGENT,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return [a for a in data.get("accounts", []) if a.get("product") == "bc3"]


def save_config(token_data: dict, accounts: list[dict]):
    """Save tokens and account info to config file."""
    config = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "accounts": [
            {"id": a["id"], "name": a["name"], "href": a["href"]}
            for a in accounts
        ],
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\nConfig saved to {CONFIG_FILE}")
    print(f"Found {len(accounts)} Basecamp 4 account(s):")
    for a in accounts:
        print(f"  - {a['name']} (ID: {a['id']})")


def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return json.load(f)


def run_oauth_flow():
    """Run the full OAuth authorization flow."""
    server = HTTPServer(("localhost", 9292), CallbackHandler)
    server.auth_code = None

    print("Opening browser for Basecamp authorization...")
    webbrowser.open(AUTH_URL)
    print("Waiting for callback on http://localhost:9292/callback ...")

    # Wait for the single callback request
    while server.auth_code is None:
        server.handle_request()

    code = server.auth_code
    server.server_close()
    print(f"Got authorization code: {code[:12]}...")

    print("Exchanging code for tokens...")
    token_data = exchange_code(code)
    print("Got access token!")

    accounts = get_accounts(token_data["access_token"])
    save_config(token_data, accounts)


def run_refresh():
    """Refresh the stored access token."""
    config = load_config()
    if not config.get("refresh_token"):
        print("No refresh token found. Run `python auth.py` first.")
        sys.exit(1)

    print("Refreshing access token...")
    token_data = refresh_token(config["refresh_token"])
    config["access_token"] = token_data["access_token"]
    if "refresh_token" in token_data:
        config["refresh_token"] = token_data["refresh_token"]

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print("Token refreshed and saved!")


def identify_user(access_token: str) -> dict | None:
    """Use the token to find who this user is (name, email, user IDs per account)."""
    resp = requests.get(
        "https://launchpad.37signals.com/authorization.json",
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": USER_AGENT,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "name": data["identity"]["first_name"] + " " + data["identity"]["last_name"],
        "email": data["identity"]["email_address"],
        "identity_id": data["identity"]["id"],
    }


def _save_user_token(code: str):
    """Exchange a code for tokens, identify the user, and save to config."""
    config = load_config()

    print("Exchanging code for tokens...")
    token_data = exchange_code(code)

    user_info = identify_user(token_data["access_token"])
    print(f"Authorized: {user_info['name']} ({user_info['email']})")

    user_tokens = config.setdefault("user_tokens", {})
    user_tokens[user_info["email"]] = {
        "name": user_info["name"],
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Token saved! ({len(user_tokens)} user(s) total in user_tokens)")
    for email, info in user_tokens.items():
        print(f"  - {info['name']} ({email})")


def run_add_user():
    """Authorize an additional user (local callback server)."""
    print("=" * 50)
    print("Add a team member's token (local mode)")
    print("The team member must be on this computer.")
    print("=" * 50)

    server = HTTPServer(("localhost", 9292), CallbackHandler)
    server.auth_code = None

    print("Opening browser for Basecamp authorization...")
    webbrowser.open(AUTH_URL)
    print("Waiting for callback on http://localhost:9292/callback ...")

    while server.auth_code is None:
        server.handle_request()

    server.server_close()
    _save_user_token(server.auth_code)


def run_add_remote():
    """Add a remote user's token by pasting the code they send you.

    Flow:
      1. Send the auth URL to the team member
      2. They click authorize in their browser
      3. Browser redirects to localhost:9292/callback?code=XXXXX (page won't load)
      4. They copy the code from the URL bar and send it to you
      5. You paste it here
    """
    print("=" * 50)
    print("Add a remote team member's token")
    print()
    print("Send them this link:")
    print(f"  {AUTH_URL}")
    print()
    print("After they click 'Yes, I'll allow access', their browser")
    print("will try to load localhost and FAIL — that's expected.")
    print("Ask them to copy the 'code=XXXXX' value from the URL bar.")
    print()
    print("The URL will look like:")
    print("  http://localhost:9292/callback?code=abc123def456...")
    print("They should send you everything after 'code='")
    print("=" * 50)

    raw = input("\nPaste the code (or full URL) here: ").strip()
    if not raw:
        print("No code provided.")
        sys.exit(1)

    # Extract code from full URL if pasted
    if "code=" in raw:
        code = parse_qs(urlparse(raw).query).get("code", [raw])[0]
    else:
        code = raw

    _save_user_token(code)


def run_import_token():
    """Import a token JSON string from get_my_token.py output."""
    config = load_config()
    print("Paste the JSON output from get_my_token.py:")
    raw = input().strip()
    if not raw:
        print("No input provided.")
        sys.exit(1)

    data = json.loads(raw)
    user_tokens = config.setdefault("user_tokens", {})
    user_tokens[data["email"]] = {
        "name": data["name"],
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Imported: {data['name']} ({data['email']})")
    print(f"{len(user_tokens)} user(s) total:")
    for email, info in user_tokens.items():
        print(f"  - {info['name']} ({email})")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "refresh":
        run_refresh()
    elif len(sys.argv) > 1 and sys.argv[1] == "add-user":
        run_add_user()
    elif len(sys.argv) > 1 and sys.argv[1] == "add-remote":
        run_add_remote()
    elif len(sys.argv) > 1 and sys.argv[1] == "import-token":
        run_import_token()
    else:
        run_oauth_flow()
