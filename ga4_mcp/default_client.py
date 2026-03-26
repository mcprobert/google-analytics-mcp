"""Embedded OAuth client credentials for zero-config setup.

These credentials allow users to authenticate with their Google account
without needing their own Google Cloud project or OAuth client secrets.

The maintainer creates one OAuth client ID (type: "Desktop application")
in their Google Cloud Console and embeds the values here. All users of
the package then use these shared credentials to authenticate.

Setup (one-time, by the package maintainer):
1. Go to Google Cloud Console → APIs & Services → Credentials
2. Create an OAuth 2.0 Client ID (Application type: Desktop app)
3. Enable the GA4 Data API and GA4 Admin API on the project
4. Configure the OAuth consent screen and get it verified for production
5. Paste the client_id and client_secret below
"""

# Replace these with your actual OAuth client credentials from Google Cloud Console.
# These are safe to embed in distributed applications — Google's OAuth for
# "Desktop application" type clients does not require the client_secret to be
# kept confidential (see: Google OAuth2 for Installed Applications).
CLIENT_ID = "355080528088-n58clrv4038i8m9mvq8ot4f6co769m3m.apps.googleusercontent.com"
CLIENT_SECRET = "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"

# Do not modify below this line
CLIENT_CONFIG = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}


def get_default_client_config() -> dict | None:
    """Return the embedded client config, or None if not configured."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return None
    return CLIENT_CONFIG["installed"]
