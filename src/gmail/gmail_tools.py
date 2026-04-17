from typing import Optional, List

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from gmail_manager import GmailManager
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GM = GmailManager()

class FetchEmailsInput(BaseModel):
    """
    Input schema for the fetch_emails_tool.
    """
    email_address: str = Field(description="Email address to fetch emails for")
    minutes_since: int = Field(
        default=30,
        description="Only retrieve emails no older than this many minutes."
    )

@tool(args_schema=FetchEmailsInput)
def fetch_emails_tool(email_address: str, minutes_since: int = 30) -> str:
    """
    Fetches recent emails from Gmail for the specified email address.

    Args:
        email_address: Email address to fetch messages for
        minutes_since: Only retrieve emails newer than this many minutes (default: 30)

    Returns:
        String summary of fetched emails
    """
    emails = GM.get_emails(recipient=email_address, max_age_minutes=minutes_since, unread_only=False)

    if not emails:
        return "No new emails found."

    result = f"Found {len(emails)} new emails:\n\n"

    for i, email in enumerate(emails, 1):
        if email.reply_to:
            result += f"{i}. You already responded to this email (Thread ID: {email.thread_id})\n\n"
            continue

        result += f"{i}. From: {email.sender}\n"
        result += f"   To: {email.recipient}\n"
        result += f"   Subject: {email.subject}\n"
        result += f"   Time: {email.sent}\n"
        result += f"   ID: {email.id}\n"
        result += f"   Thread ID: {email.thread_id}\n"
        result += f"   Content: {email.body[:200]}...\n\n"

    return result

class ReplyEmailInput(BaseModel):
    """
    Input schema for the reply_email_tool.
    """
    email_id: str = Field(
        description="Gmail message ID to reply to. This must be a valid Gmail message ID obtained from the fetch_emails_tool."
    )
    response_text: str = Field(
        description="Content of the reply"
    )
    additional_recipients: Optional[List[str]] = Field(
        default=None,
        description="Optional additional recipients to include"
    )

@tool(args_schema=ReplyEmailInput)
def reply_email_tool(
        email_id: str,
        response_text: str,
        additional_recipients: Optional[List[str]] = None
) -> str:
    """
    Send a reply to an existing email thread in Gmail.

    Args:
        email_id: Gmail message ID to reply to. This should be a valid Gmail message ID obtained from the fetch_emails_tool.
        response_text: Content of the reply or new email
        additional_recipients: Optional additional recipients to include

    Returns:
        Confirmation message
    """
    try:
        success = GM.reply_to_email_by_id(email_id, response_text, cc=additional_recipients)
        if success:
            return f"Email reply sent successfully to message ID: {email_id}"
        else:
            return "Failed to send email due to an API error"
    except Exception as e:
        return f"Failed to send email: {str(e)}"

class SendNewEmailInput(BaseModel):
    """
    Input schema for the reply_email_tool.
    """
    recipient: str = Field(
        description="Email address of the intended recipient"
    )
    subject: str = Field(
        description="Subject of the email"
    )
    body_text: str = Field(
        description="The main content of the email"
    )
    additional_recipients: Optional[List[str]] = Field(
        default=None,
        description="Optional additional recipients to cc the email to"
    )

@tool(args_schema=SendNewEmailInput)
def send_new_email_tool(
        recipient: str,
        subject: str,
        body_text: str,
        additional_recipients: Optional[List[str]] = None
) -> str:
    """
    Send a new email in Gmail.

    Args:
        recipient: Email address of the recipient
        subject: Subject of the email
        body_text: Content of the email
        additional_recipients: Optional additional recipients to include

    Returns:
        Confirmation message
    """
    try:
        success = GM.send_new_email(recipient=recipient, subject=subject, body=body_text, cc=additional_recipients)
        if success:
            return f"Email sent successfully to recipient: {recipient}"
        else:
            return "Failed to send email due to an API error"
    except Exception as e:
        return f"Failed to send email: {str(e)}"

class CheckCalendarInput(BaseModel):
    """
    Input schema for the check_calendar_tool.
    """
    dates: List[str] = Field(
        description="List of dates to check in YYYY-MM-DD format"
    )

def all_day(start: datetime, end: datetime) -> bool:
    """
    Check if an event spans the entire day.
    """
    return (start.hour == 0 and start.minute == 0 and start.second == 0 and start.microsecond == 0 and
            end == start + timedelta(days=1))

@tool(args_schema=CheckCalendarInput)
def check_calendar_tool(dates: List[str]) -> str:
    """
    Check Google Calendar for events on specified dates.

    Args:
        dates: List of dates to check in YYYY-MM-DD format

    Returns:
        Formatted calendar events for the specified dates
    """
    try:
        tz: ZoneInfo = ZoneInfo(GM.get_calendar_timezone())

        # 1. Convert argument dates to datetimes in sorted order (aware)
        sorted_datetimes = sorted([datetime.strptime(d, "%Y-%m-%d") for d in dates])

        # 2. Obtain a list of CalendarEvent objects
        events = GM.get_calendar_events(sorted_datetimes)

        # 3. Generate a dict mapping date to sorted CalendarEvent list
        events_by_date = {}
        for dt in sorted_datetimes:
            dt_key = dt.strftime("%Y-%m-%d")
            events_by_date[dt_key] = []

        for event in events:
            # CalendarEvent has a start field which is a datetime
            event_date_key = event.start.strftime("%Y-%m-%d")
            # Find which requested date this event belongs to
            # Note: get_calendar_events returns events for those dates, but some events might span multiple days
            # or start on one of the requested dates.
            if event_date_key in events_by_date:
                events_by_date[event_date_key].append(event)
            else:
                logger.warning(f"Event for {event_date_key} not found in requested dates")

        # Sort events for each day by start time
        for date_key in events_by_date:
            events_by_date[date_key].sort(key=lambda x: x.start)

        # 4. Iterate through sorted datetimes to generate result string
        result = "Calendar events:\n\n"
        for dt in sorted_datetimes:
            date_str = dt.strftime("%Y-%m-%d")
            day_events = events_by_date[date_str]
            result += f"Events for {date_str}:\n"
            if not day_events:
                result += "  No events found for this day\n"
                result += "  Available all day\n\n"
            else:
                busy_slots = []
                for event in day_events:
                    if all_day(event.start, event.end):
                        result += f"  - All day: {event.summary}\n"
                        busy_slots.append(("all-day", "all-day"))

                    else:
                        busy_slots.append((event.start, event.end))
                        # Format for display
                        start_display = event.start.strftime("%I:%M %p")
                        end_display = event.end.strftime("%I:%M %p")
                        result += f"  - {start_display} - {end_display}: {event.summary}\n"

                # Calculate available slots
                if "all-day" in [slot[0] for slot in busy_slots]:
                    result += "  Available: No availability (all-day events)\n\n"
                else:
                    # Sort busy slots by start time
                    busy_slots.sort(key=lambda x: x[0])

                    # Define working hours (9 AM to 5 PM)
                    # Note: Working hours are currently hardcoded for simplicity
                    # In production, this could be made configurable per user/organization
                    work_start = dt.replace(
                        hour = 9,
                        minute = 0,
                        second = 0,
                        microsecond = 0,
                        tzinfo = tz
                    )

                    work_end = dt.replace(
                        hour = 17,
                        minute = 0,
                        second = 0,
                        microsecond = 0,
                        tzinfo = tz
                    )

                    # Calculate available slots
                    available_slots = []
                    current = work_start

                    for start, end in busy_slots:
                        if current < start:
                            available_slots.append((current, start))
                        current = max(current, end)

                    if current < work_end:
                        available_slots.append((current, work_end))

                    # Format available slots
                    if available_slots:
                        result += "  Available: "
                        for i, (start, end) in enumerate(available_slots):
                            start_display = start.strftime("%I:%M %p")
                            end_display = end.strftime("%I:%M %p")
                            result += f"{start_display} - {end_display}"
                            if i < len(available_slots) - 1:
                                result += ", "
                        result += "\n\n"
                    else:
                        result += "  Available: No availability during working hours\n\n"

        return result

    except Exception as e:
        return f"Failed to check calendar: {str(e)}"


class ScheduleMeetingInput(BaseModel):
    """
    Input schema for the schedule_meeting_tool.
    """
    attendees: List[str] = Field(
        description="Email addresses of meeting attendees"
    )
    title: str = Field(
        description="Meeting title/subject"
    )
    start_time: str = Field(
        description="Meeting start time in ISO format (YYYY-MM-DDTHH:MM:SS)"
    )
    end_time: str = Field(
        description="Meeting end time in ISO format (YYYY-MM-DDTHH:MM:SS)"
    )
    organizer_email: str = Field(
        description="Email address of the meeting organizer"
    )
    timezone: str = Field(
        default="America/Los_Angeles",
        description="Timezone for the meeting"
    )

@tool(args_schema=ScheduleMeetingInput)
def schedule_meeting_tool(
        attendees: List[str],
        title: str,
        start_time: str,
        end_time: str,
        organizer_email: str,
        timezone: str = None
) -> str:
    """
    Schedule a meeting with Google Calendar and send invites.

    Args:
        attendees: Email addresses of meeting attendees
        title: Meeting title/subject
        start_time: Meeting start time in ISO format (YYYY-MM-DDTHH:MM:SS)
        end_time: Meeting end time in ISO format (YYYY-MM-DDTHH:MM:SS)
        organizer_email: Email address of the meeting organizer
        timezone: Timezone for the meeting (default: America/Los_Angeles)

    Returns:
        Success or failure message
    """
    try:

        if timezone is None:
            timezone = GM.get_calendar_timezone()

        success = GM.send_calendar_invitation(
            title=title,
            start_time=start_time,
            end_time=end_time,
            attendees=attendees,
            organizer=organizer_email,
            tz=timezone
        )

        if success:
            return f"Meeting '{title}' scheduled successfully from {start_time} to {end_time} with {len(attendees)} attendees"
        else:
            return "Failed to schedule meeting"
    except Exception as e:
        return f"Error scheduling meeting: {str(e)}"

if __name__ == "__main__":

    from dotenv import load_dotenv
    load_dotenv()

    from langchain.chat_models import init_chat_model
    import os

    model = f"ollama:{os.getenv('OLLAMA_MODEL')}"
    llm = init_chat_model(model, base_url=os.getenv('OLLAMA_BASE_URL'))

    agent = create_agent(
        model=llm,
        tools=[fetch_emails_tool, reply_email_tool, send_new_email_tool, check_calendar_tool, schedule_meeting_tool],
        system_prompt=(
            "You are a helpful assistant that helps users manage their Gmail "
            "account. You can fetch emails, reply to emails, send new emails, "
            "check calendar availability, and schedule meetings."
        )
    )

    message = HumanMessage(content=f"Fetch my emails from the past 240 minutes. My email address is {os.getenv('EMAIL_ADDRESS')}")

    response = agent.invoke({
        "messages": [message]
    })

    print(response["messages"][-1].content)