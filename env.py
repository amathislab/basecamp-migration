"""Load configuration from .env file."""

import os

_ENV_FILE = ".env"


def _load_dotenv():
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
REDIRECT_URI = os.environ.get("REDIRECT_URI", "http://localhost:9292/callback")
USER_AGENT = os.environ.get("USER_AGENT", "BACMigration")
SOURCE_ACCOUNT = int(os.environ["SOURCE_ACCOUNT"])
DEST_ACCOUNT = int(os.environ["DEST_ACCOUNT"])

AUTH_URL = (
    "https://launchpad.37signals.com/authorization/new"
    f"?type=web_server&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
)
TOKEN_URL = "https://launchpad.37signals.com/authorization/token"
