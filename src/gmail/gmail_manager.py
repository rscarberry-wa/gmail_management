import os.path
from dataclasses import dataclass
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import logging
from dotenv import load_dotenv
import os

load_dotenv()

CREDENTIALS_DIR = os.getenv("GMAIL_CREDENTIALS_PATH", Path(__file__).parent.absolute() / ".credentials")

# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks"
]

@dataclass
class Email:
    id: str
    thread_id: str
    message_id: str
    subject: str
    sender: str
    recipient: str
    sent: datetime | None
    body: str
    snippet: str
    labels: list[str]
    reply_to: bool

@dataclass
class CalendarEvent:
    id: str
    summary: str
    start: datetime
    end: datetime
    description: str | None = None
    location: str | None = None

@dataclass
class CalendarTask:
    id: str
    title: str
    due: datetime | None = None
    notes: str | None = None
    status: str | None = None

@dataclass
class GmailManager:
    def __init__(self, credentials_dir: str = CREDENTIALS_DIR):
        self.logger = self._setup_logging()
        self.credentials_dir = Path(credentials_dir)

    @staticmethod
    def _setup_logging():
        logger = logging.getLogger(__name__)
        if not logger.handlers:  # Prevent duplicate handlers
            logger.setLevel(logging.INFO)
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    def _get_credentials(self, gmail_token: dict | str | None = None) -> Credentials:

        token_dict = None

        # 1. Get credentials data from provided token if available.
        if gmail_token is not None:
            try:
                token_dict = json.loads(gmail_token) if isinstance(gmail_token, str) else gmail_token
                self.logger.info("Loading credentials directly provided gmail_token parameter")
            except Exception as e:
                self.logger.error(f"Error parsing provided gmail_token: {str(e)}")

        # 2. Attempt the environment variable.
        if token_dict is None:
            env_token = os.getenv("GMAIL_TOKEN")
            if env_token is not None:
                try:
                    token_dict = json.loads(env_token)
                    self.logger.info("Loading credentials from environment variable")
                except Exception as e:
                    self.logger.error(f"Error parsing environment variable GMAIL_TOKEN: {str(e)}")

        # 3. Attempt the token.json.
        if token_dict is None:
            token_path = self.credentials_dir / "token.json"
            if token_path.exists():
                self.logger.info("Loading credentials from credentials file")
                try:
                    with open(token_path, "r") as f:
                        token_dict = json.load(f)
                    self.logger.info("Loading credentials from credentials file")
                except Exception as e:
                    self.logger.error(f"Error reading credentials file: {str(e)}")

        creds = None
        save_token_data = False

        if token_dict is not None:
            creds = Credentials(
                token=token_dict.get("token"),
                refresh_token=token_dict.get("refresh_token"),
                token_uri=token_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=token_dict.get("client_id"),
                client_secret=token_dict.get("client_secret"),
                scopes=token_dict.get("scopes", ["https://www.googleapis.com/auth/gmail.modify"])
            )
        else:
            creds_path = self.credentials_dir / "credentials.json"
            if creds_path.exists():
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
                    creds = flow.run_local_server(port=0)
                    save_token_data = True
                    self.logger.info("Loading credentials from credentials file")
                except Exception as e:
                    self.logger.error(f"Error reading credentials file: {str(e)}")

        if creds is None:
            raise ValueError(
                "No valid credentials found. Please provide a valid gmail_token "
                "or set the GMAIL_TOKEN or GMAIL_CREDENTIALS_PATH environment variables."
            )

        # Check if the scopes in the credentials match the required SCOPES
        if not creds.scopes or not all(scope in creds.scopes for scope in SCOPES):
            self.logger.info("Credentials have insufficient scopes. Re-authentication required.")
            creds_path = self.credentials_dir / "credentials.json"
            if creds_path.exists():
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
                    creds = flow.run_local_server(port=0)
                    save_token_data = True
                    self.logger.info("Re-authenticated with updated scopes.")
                except Exception as e:
                    self.logger.error(f"Error during re-authentication: {str(e)}")
                    raise ValueError(f"Insufficient scopes and failed to re-authenticate: {str(e)}")
            else:
                raise ValueError("Insufficient scopes and credentials.json not found for re-authentication.")

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                save_token_data = True
            else:
                raise ValueError("Invalid credentials. Please re-authenticate.")

        # To make compatible with legacy code
        creds.authorize = lambda request: request

        if save_token_data and self.credentials_dir.exists():
            token_path = self.credentials_dir / "token.json"
            with open(token_path, "w") as token:
                token.write(creds.to_json())
                logging.info("Token data saved.")

        return creds

    def get_email_address(self) -> str:
        """
        Retrieves the email address associated with the Gmail API credentials.

        :return: The email address as a string.
        """
        try:
            creds = self._get_credentials()
            service = build("gmail", "v1", credentials=creds)
            profile = service.users().getProfile(userId='me').execute()
            return profile.get('emailAddress')
        except Exception as e:
            self.logger.error(f"Error retrieving email address: {str(e)}")
            return ""

    def get_emails(self,
                   recipient: str = None,
                   senders: set[str] | str | None = None,
                   max_age_minutes: int = 60,
                   unread_only: bool = True,
                   filter_latest: bool = True,
                   gmail_token: dict | str | None = None
                   ) -> list[Email]:
        """
        Fetches emails based on the specified criteria using the Gmail API.

        This function retrieves emails either sent to a specific recipient or sent by specific
        senders, within a defined time span. It provides additional options to filter for
        unread messages, and to optionally retrieve only the most recent emails in a thread.

        :param recipient: An optional email address to filter emails sent to the specified recipient.
                          If not provided, defaults to "me", representing the authenticated user's email.
        :type recipient: str, optional
        :param senders: An optional set or string of email addresses to filter the senders' addresses.
                        If a string is provided, it will be treated as a single sender.
        :param max_age_minutes: The maximum age, in minutes, for retrieving emails. Emails sent before
                                the specified time will be excluded from the results.
                                Defaults to 60 minutes.
        :type max_age_minutes: int, optional
        :param unread_only: A flag indicating whether only unread emails should be fetched.
                            If set to True, excludes emails that have already been marked as read.
                            Defaults to True.
        :type unread_only: bool, optional
        :param filter_latest: A flag indicating whether to retrieve only the most recent emails
                              in a thread. When set to True, excludes older emails within the same
                              thread. Defaults to True.
        :type filter_latest: bool, optional
        :param gmail_token: Credentials or token required for authenticating with the Gmail API.
                            Can be passed as a dictionary or string.
        :type gmail_token: dict | str, optional
        :return: A list of `Email` objects containing the retrieved email details, such as sender,
                 recipient, subject, body, sent time, and additional metadata. If the retrieval
                 fails, an empty list is returned.
        :rtype: list[Email]
        """
        try:
            creds = self._get_credentials(gmail_token)
            service = build("gmail", "v1", credentials=creds)

            if recipient is None:
                recipient = "me"
            
            # Constructing the query
            queries = []
            queries.append(f"to:{recipient}")
            
            if senders:
                if isinstance(senders, str):
                    senders = [senders]
                sender_query = " OR ".join([f"from:{s}" for s in senders])
                if len(senders) > 1:
                    sender_query = f"({sender_query})"
                queries.append(sender_query)
            
            # Join with OR as per instructions: "to specified recipient OR from anyone in senders"
            query = " OR ".join(queries)
            
            if unread_only:
                query = f"({query}) is:unread"
            
            # Add time constraint
            after_date = datetime.now() - timedelta(minutes=max_age_minutes)
            after_timestamp = int(after_date.timestamp())
            query = f"({query}) after:{after_timestamp}"
            
            self.logger.info(f"Fetching emails with query: {query}")
            
            results = service.users().messages().list(userId='me', q=query).execute()
            messages = results.get('messages', [])
            
            user_email = ""
            if filter_latest:
                profile = service.users().getProfile(userId='me').execute()
                user_email = profile.get('emailAddress', '')

            emails = []
            for message in messages:
                msg_id = message['id']
                thread_id = message['threadId']
                msg = service.users().messages().get(userId='me', id=msg_id).execute()
                
                # Get thread to check for replies (logic from get_emails)
                thread = service.users().threads().get(userId='me', id=thread_id).execute()
                
                # Check if it's the latest in thread
                is_latest = not any(
                    int(m['internalDate']) > int(msg['internalDate']) 
                    for m in thread.get('messages', [])
                )

                has_been_replied_to = any(
                    'SENT' in m.get('labelIds', []) for m in thread.get('messages', [])
                    if int(m['internalDate']) > int(msg['internalDate'])
                )

                headers = msg.get('payload', {}).get('headers', [])
                message_id = next((h['value'] for h in headers if h['name'].lower() == 'message-id'), '')
                subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
                sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')

                if filter_latest:
                    # Only include if NOT from user AND is the latest
                    if user_email and user_email.lower() in sender.lower():
                        continue
                    if not is_latest:
                        continue

                to_header = next((h['value'] for h in headers if h['name'].lower() == 'to'), 'Unknown Recipient')
                when_sent_str = next((h['value'] for h in headers if h['name'].lower() == 'date'), 'Unknown Date')
                
                when_sent = parsedate_to_datetime(when_sent_str) if when_sent_str != 'Unknown Date' else None
                labels = msg.get('labelIds', [])
                snippet = msg.get('snippet', '')
                
                # Extract body text
                body = ""
                payload = msg.get('payload', {})
                parts = [payload]
                plain_text_parts = []
                html_parts = []
                
                while parts:
                    part = parts.pop(0)
                    if part.get('parts'):
                        parts.extend(part.get('parts'))
                    
                    mime_type = part.get('mimeType')
                    if 'data' in part.get('body', {}):
                        try:
                            data = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                            if mime_type == 'text/plain':
                                plain_text_parts.append(data)
                            elif mime_type == 'text/html':
                                html_parts.append(data)
                        except Exception as e:
                            self.logger.warning(f"Failed to decode part: {e}")

                if plain_text_parts:
                    body = "".join(plain_text_parts)
                elif html_parts:
                    body = "".join(html_parts)
                else:
                    body = snippet

                emails.append(Email(
                    id=msg_id,
                    thread_id=thread_id,
                    message_id=message_id,
                    subject=subject,
                    sender=sender,
                    recipient=to_header,
                    sent=when_sent,
                    body=body,
                    snippet=snippet,
                    labels=labels,
                    reply_to=has_been_replied_to
                ))
            
            return emails
            
        except Exception as e:
            self.logger.error(f"Failed to fetch emails: {e}")
            return []

    def reply_to_email_by_id(self,
                             email_id: str, 
                             reply_text: str,
                             attachments: list[str] | None = None,
                             cc: list[str] | None = None,
                             bcc: list[str] | None = None
                             ) -> bool:
        """
        Retrieves an email by its ID and replies to it.

        Args:
            email_id: The ID of the email to reply to.
            reply_text: The text content of the reply.
        """
        try:
            creds = self._get_credentials()
            service = build("gmail", "v1", credentials=creds)

            # Retrieve the message
            msg = service.users().messages().get(userId='me', id=email_id).execute()

            # Extract necessary fields for Email object
            headers = msg.get('payload', {}).get('headers', [])
            message_id = next((h['value'] for h in headers if h['name'].lower() == 'message-id'), '')
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')
            to_header = next((h['value'] for h in headers if h['name'].lower() == 'to'), 'Unknown Recipient')
            when_sent_str = next((h['value'] for h in headers if h['name'].lower() == 'date'), 'Unknown Date')
            when_sent = parsedate_to_datetime(when_sent_str) if when_sent_str != 'Unknown Date' else None
            labels = msg.get('labelIds', [])
            snippet = msg.get('snippet', '')
            thread_id = msg.get('threadId', '')

            # Extract body (simplified as we just need the Email object to pass to reply_to_email)
            # and reply_to_email doesn't seem to use the body of the original email.
            body = snippet 

            email_obj = Email(
                id=email_id,
                thread_id=thread_id,
                message_id=message_id,
                subject=subject,
                sender=sender,
                recipient=to_header,
                sent=when_sent,
                body=body,
                snippet=snippet,
                labels=labels,
                reply_to=False # Default to False, will be determined if needed
            )

            return self.reply_to_email(email_obj, reply_text, attachments, cc, bcc)

        except Exception as e:
            self.logger.error(f"Failed to reply to email by id {email_id}: {e}")
            return None

    def reply_to_email(self, 
                       email: Email,
                       reply_text: str,
                       attachments: list[str] | None = None,
                       cc: list[str] | None = None,
                       bcc: list[str] | None = None
                       ) -> bool:
        """
        Replies to an email.

        Args:
            email: The Email object to reply to.
            reply_text: The text content of the reply.
            attachments (list[str], optional): List of file paths for attachments. Defaults to None.
            cc (list[str], optional): List of email addresses for CC recipients. Defaults to None.
            bcc (list[str], optional): List of email addresses for BCC recipients. Defaults to None.
        """
        try:
            attachments = attachments or []
            cc = cc or []
            bcc = bcc or []

            creds = self._get_credentials()
            service = build("gmail", "v1", credentials=creds)

            if attachments:
                message = MIMEMultipart()
                message.attach(MIMEText(reply_text))
            else:
                message = MIMEText(reply_text)
            
            # Use original subject with 'Re: ' prefix if not already present
            subject = email.subject
            if not subject.lower().startswith('re:'):
                subject = f"Re: {subject}"
            
            message['to'] = email.sender
            message['subject'] = subject
            
            if cc:
                message['cc'] = ", ".join(cc)
            if bcc:
                message['bcc'] = ", ".join(bcc)

            # Set headers for threading
            if email.message_id:
                message['In-Reply-To'] = email.message_id
                message['References'] = email.message_id

            for attachment_path in attachments:
                path = Path(attachment_path)
                if not path.exists():
                    self.logger.warning(f"Attachment not found: {attachment_path}")
                    continue
                
                part = MIMEBase('application', 'octet-stream')
                with open(path, 'rb') as f:
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    'Content-Disposition',
                    f'attachment; filename="{path.name}"'
                )
                message.attach(part)
            
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            
            reply_body = {
                'raw': raw_message,
                'threadId': email.thread_id
            }

            # Format: {'id': '19d97964b22e33d7', 'threadId': '19d978bc63c7e61c', 'labelIds': ['SENT']}
            sent_message = service.users().messages().send(userId='me', body=reply_body).execute()

            self.logger.info(f"Reply sent successfully. Message ID: {sent_message['id']}")

            return True

        except HttpError as error:
            self.logger.error(f"An error occurred while sending reply: {error}")
            return False
        
    def send_new_email(self,
                       recipient: str,
                       subject: str,
                       body: str,
                       attachments: list[str] | None = None,
                       cc: list[str] | None = None,
                       bcc: list[str] | None = None) -> bool:
        """
        Sends a new email with optional attachments, CC, and BCC recipients.
        
        Args:
            recipient (str): The email address of the recipient.
            subject (str): The subject of the email.
            body (str): The body content of the email.
            attachments (list[str], optional): List of file paths for attachments. Defaults to None.
            cc (list[str], optional): List of email addresses for CC recipients. Defaults to None.
            bcc (list[str], optional): List of email addresses for BCC recipients. Defaults to None.
        Returns:
            bool: True if the email was sent successfully, False otherwise.
        """

        try:
            attachments = attachments or []
            cc = cc or []
            bcc = bcc or []
            
            creds = self._get_credentials()
            service = build("gmail", "v1", credentials=creds)

            if attachments:
                message = MIMEMultipart()
                message.attach(MIMEText(body))
            else:
                message = MIMEText(body)

            message['to'] = recipient
            message['subject'] = subject
            
            if cc:
                message['cc'] = ", ".join(cc)
            if bcc:
                message['bcc'] = ", ".join(bcc)

            for attachment_path in attachments:
                path = Path(attachment_path)
                if not path.exists():
                    self.logger.warning(f"Attachment not found: {attachment_path}")
                    continue
                
                part = MIMEBase('application', 'octet-stream')
                with open(path, 'rb') as f:
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    'Content-Disposition',
                    f'attachment; filename="{path.name}"'
                )
                message.attach(part)

            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            send_body = {'raw': raw_message}
            
            sent_message = service.users().messages().send(userId='me', body=send_body).execute()
            self.logger.info(f"Email sent successfully. Message ID: {sent_message['id']}")
            return True

        except Exception as e:
            self.logger.error(f"An error occurred while sending email: {str(e)}")
            return False

    def get_calendar_timezone(self) -> str:
        """
        Retrieves the primary calendar's timezone.
        
        :return: The IANA timezone string (e.g., 'America/New_York').
            Defaults to 'UTC' if an error occurs.
        """
        try:
            creds = self._get_credentials()
            service = build("calendar", "v3", credentials=creds)
            calendar = service.calendars().get(calendarId='primary').execute()
            return calendar.get('timeZone', 'UTC')
        except Exception as e:
            self.logger.error(f"Error fetching calendar timezone: {str(e)}")
            return 'UTC'

    def get_calendar_events(self, dates: list[str] | list[datetime]) -> list[CalendarEvent]:
        """
        Retrieves calendar events for the specified dates.

        :param dates: List of dates for which to retrieve events. Can be strings in 'YYYY-MM-DD' format or datetime objects.
        :return: List of CalendarEvent objects for the specified dates.
        """
        try:
            creds = self._get_credentials()
            service = build("calendar", "v3", credentials=creds)
            
            # Get the calendar's timezone for all-day events
            calendar_tz_str = self.get_calendar_timezone()
            calendar_tz = ZoneInfo(calendar_tz_str)
            
            all_events = []
            seen_event_ids = set()
            
            # Prepare requested dates for comparison
            requested_dates = []
            for date_item in dates:
                if isinstance(date_item, str):
                    requested_dates.append(datetime.strptime(date_item, "%Y-%m-%d").date())
                else:
                    requested_dates.append(date_item.date())

            for date_item in dates:
                if isinstance(date_item, str):
                    dt = datetime.strptime(date_item, "%Y-%m-%d")
                else:
                    dt = date_item
                
                # Set time to start and end of day
                time_min = dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
                time_max = dt.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat() + 'Z'
                
                events_result = service.events().list(
                    calendarId='primary',
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                
                events = events_result.get('items', [])
                
                for event in events:
                    event_id = event.get('id')
                    if event_id in seen_event_ids:
                        continue
                    
                    # Google Calendar API returns 'dateTime' for events with time and 'date' for all-day events
                    start_info = event['start'].get('dateTime', event['start'].get('date'))
                    end_info = event['end'].get('dateTime', event['end'].get('date'))
                    
                    # Parse start and end (simple isoformat handling)
                    # Note: all-day events are YYYY-MM-DD
                    if 'T' in start_info:
                        start_dt = datetime.fromisoformat(start_info.replace('Z', '+00:00'))
                    else:
                        # All-day events are naive in the calendar's timezone
                        start_dt = datetime.strptime(start_info, "%Y-%m-%d").replace(tzinfo=calendar_tz)
                        
                    if 'T' in end_info:
                        end_dt = datetime.fromisoformat(end_info.replace('Z', '+00:00'))
                    else:
                        # All-day events are naive in the calendar's timezone
                        end_dt = datetime.strptime(end_info, "%Y-%m-%d").replace(tzinfo=calendar_tz)

                    # Omit events where the end time is at the very beginning of one of the dates 
                    # but the start time is not in one of the dates.
                    # This typically happens when an event ends exactly at 00:00:00 of a day.
                    if end_dt.time() == datetime.min.time() and end_dt.date() in requested_dates:
                        if start_dt.date() not in requested_dates:
                            continue

                    all_events.append(CalendarEvent(
                        id=event_id,
                        summary=event.get('summary', 'No Summary'),
                        start=start_dt,
                        end=end_dt,
                        description=event.get('description'),
                        location=event.get('location')
                    ))
                    if event_id:
                        seen_event_ids.add(event_id)
            
            return all_events
            
        except Exception as e:
            self.logger.error(f"An error occurred while retrieving calendar items: {str(e)}")
            return []

    def send_calendar_invitation(self,
                                 title: str,
                                 start_time: str | datetime,
                                 end_time: str | datetime,
                                 attendees: list[str],
                                 organizer: str,
                                 tz: str = "America/New_York"
                                 ) -> bool:
        """
        Sends a calendar invitation to the specified attendees with the provided details. This function schedules
        an event and sends an invitation to each attendee's email address.

        Parameters:
        title : str
            The title of the calendar event.

        start_time : str or datetime
            The start time of the event, specified as a string or a datetime object.

        end_time : str or datetime
            The end time of the event, specified as a string or a datetime object.

        attendees : list[str]
            A list of email addresses of the attendees who will be invited.

        organizer : str
            The email address of the event organizer.

        tz : str, optional
            The timezone in which the event is scheduled, default is "America/New_York".

        Returns:
            True on success, False otherwise.

        Raises:
        ValueError
            Raised if the start time is after the end time or if attendee list is empty.
        """
        if not attendees:
            raise ValueError("Attendee list cannot be empty.")

        # Convert strings to datetime if necessary
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))

        if start_time > end_time:
            raise ValueError("Start time cannot be after end time.")

        # Determine timezone handling
        start_tz_info = {}
        end_tz_info = {}

        if start_time.tzinfo is None:
            start_tz_info['timeZone'] = tz
        
        if end_time.tzinfo is None:
            end_tz_info['timeZone'] = tz

        event = {
            'summary': title,
            'organizer': {'email': organizer},
            'start': {
                'dateTime': start_time.isoformat(),
                **start_tz_info
            },
            'end': {
                'dateTime': end_time.isoformat(),
                **end_tz_info
            },
            'attendees': [{'email': email} for email in attendees],
        }

        try:
            creds = self._get_credentials()
            service = build("calendar", "v3", credentials=creds)
            service.events().insert(calendarId='primary', body=event, sendUpdates='all').execute()
            return True
        except Exception as e:
            self.logger.error(f"An error occurred while sending calendar invitation: {str(e)}")
            return False

    def get_calendar_tasks(self, dates: list[str] | list[datetime]) -> list[CalendarTask]:
        """
        Retrieves tasks from Google Calendar tasks that are due on the specified dates.

        This method queries the Google Tasks API to retrieve tasks from all task lists
        associated with the user's Google account. It filters tasks based on their due
        dates and returns a list of tasks matching the specified dates. Tasks are
        represented as `CalendarTask` objects.

        :param dates: A list of dates to filter tasks by. The dates can be provided as
            strings in the format "YYYY-MM-DD" or as datetime objects.
        :type dates: list[str] or list[datetime]
        :return: A list of `CalendarTask` objects representing tasks due on the
            specified dates. If an error occurs during task retrieval, an empty list is returned.
        :rtype: list[CalendarTask]
        """
        try:
            creds = self._get_credentials()
            service = build("tasks", "v1", credentials=creds)

            all_tasks = []
            
            # Prepare requested dates as strings for easy comparison
            requested_dates_str = []
            for d in dates:
                if isinstance(d, str):
                    requested_dates_str.append(d)
                else:
                    requested_dates_str.append(d.strftime("%Y-%m-%d"))

            # Get all task lists first
            tasklists_result = service.tasklists().list().execute()
            tasklists = tasklists_result.get('items', [])

            for tasklist in tasklists:
                page_token = None
                while True:
                    tasks_result = service.tasks().list(
                        tasklist=tasklist['id'],
                        showCompleted=True,
                        showHidden=True,
                        pageToken=page_token
                    ).execute()
                    
                    tasks = tasks_result.get('items', [])
                    
                    for task in tasks:
                        due_info = task.get('due')
                        if not due_info:
                            continue
                        
                        # Tasks API uses RFC 3339 (e.g., 2026-04-20T00:00:00.000Z)
                        due_dt = datetime.fromisoformat(due_info.replace('Z', '+00:00'))
                        due_date_str = due_dt.strftime("%Y-%m-%d")
                        
                        if due_date_str in requested_dates_str:
                            all_tasks.append(CalendarTask(
                                id=task.get('id'),
                                title=task.get('title', 'No Title'),
                                due=due_dt,
                                notes=task.get('notes'),
                                status=task.get('status')
                            ))
                    
                    page_token = tasks_result.get('nextPageToken')
                    if not page_token:
                        break

            return all_tasks

        except Exception as e:
            self.logger.error(f"An error occurred while retrieving calendar tasks: {str(e)}")
            return []


def date_range(start: str, end: str) -> list[datetime]:
    start_date = datetime.strptime(start, "%Y-%m-%d")
    end_date = datetime.strptime(end, "%Y-%m-%d")
    return [start_date + timedelta(days=x) for x in range((end_date - start_date).days + 1)]

if __name__ == "__main__":

    manager = GmailManager()

    start = datetime(
        year=2026, month=4, day=21, hour=10, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(minutes=30)

    if (manager.send_calendar_invitation(
            title="Test Event",
            start_time=start,
            end_time=end,
            attendees=["drrandys@gmail.com", "randevilabq@outlook.com"],
            organizer="drrandys@gmail.com",
            tz="America/New_York"
    )):
        print("Calendar invitation sent successfully.")
    else:
        print("Failed to send calendar invitation.")

    # print(f"email address: {manager.get_email_address()}")

    # emails = manager.get_emails(recipient="me", max_age_minutes=180, filter_latest=False)
    #
    # for email in emails:
    #     print(f"Subject: {email.subject}, email_id: {email.id}, thread_id: {email.thread_id}, message_id: {email.message_id}, sent: {email.sent}")
        
    # if manager.send_new_email(recipient="drrandys@yahoo.com", subject="Yet another test", body="Aren't you tired of these?", cc=["randevilabq@outlook.com"]):
    #     print("Email sent successfully")

    # calendar_events = manager.get_calendar_events(["2026-04-20", "2026-04-21", "2026-04-22"])
    #
    # for event in calendar_events:
    #     print(f"summary: {event.summary}, start: {event.start}, end: {event.end}, description: {event.description}")

    # calendar_tasks = manager.get_calendar_tasks(date_range("2026-04-20", "2026-04-27"))
    #
    # for task in calendar_tasks:
    #     print(task)