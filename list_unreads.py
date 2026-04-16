import os.path
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

try:
    # Works when running as a .py script
    CREDENTIALS_DIR = Path(__file__).parent / ".credentials"
except NameError:
    # Falls back when running in a Jupyter notebook
    CREDENTIALS_DIR = Path(os.getcwd()) / ".credentials"

TOKEN_JSON_PATH = CREDENTIALS_DIR / "show_labels_token.json"


def get_credentials():
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists(TOKEN_JSON_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_JSON_PATH, SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                os.path.join(CREDENTIALS_DIR, "credentials.json"), SCOPES
            )
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(TOKEN_JSON_PATH, "w") as token:
            token.write(creds.to_json())
    return creds


def list_unread_emails(service):
    """Lists unread emails in the user's inbox, paging 10 at a time."""
    page_token = None

    while True:
        try:
            # Query for unread messages in INBOX, restricting to category:primary to avoid
            # Social and Promotions messages that can make it feel like "all messages" are shown.
            results = service.users().messages().list(
                userId='me',
                q='label:INBOX is:unread category:primary',
                maxResults=10,
                pageToken=page_token
            ).execute()

            messages = results.get('messages', [])

            if not messages:
                print("No unread messages found.")
                break

            for message in messages:
                msg = service.users().messages().get(userId='me', id=message['id']).execute()

                # Extract headers for better readability
                headers = msg.get('payload', {}).get('headers', [])
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
                sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')

                print(f"ID: {message['id']} | From: {sender} | Subject: {subject}")

            page_token = results.get('nextPageToken')

            if not page_token:
                print("\nNo more pages available.")
                break

            # Simple prompt to continue paging
            user_input = input("\nPress Enter to see the next 10 emails (or 'q' to quit): ")
            if user_input.lower() == 'q':
                break

        except HttpError as error:
            print(f"An error occurred: {error}")
            break

if __name__ == "__main__":
    creds = get_credentials()
    try:
        # Call the Gmail API
        service = build("gmail", "v1", credentials=creds)
        # List unread emails, paging 10 at a time.
        list_unread_emails(service)
    except HttpError as error:
        print(f"An error occurred: {error}")