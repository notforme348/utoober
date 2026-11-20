"""Run ONCE on your own computer to get the YouTube refresh token.
pip install google-auth-oauthlib ; put client_secret.json (Desktop app) next to this file."""
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json", ["https://www.googleapis.com/auth/youtube.upload"])
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\nYT_REFRESH_TOKEN =", creds.refresh_token)
