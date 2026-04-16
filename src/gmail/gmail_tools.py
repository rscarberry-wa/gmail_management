from typing import Optional, List

from gmail_manager import GmailManager, Email
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import logging

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

# @tool(args_schema=FetchEmailsInput)
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

class SendEmailInput(BaseModel):
    """
    Input schema for the send_email_tool.
    """
    email_id: str = Field(
        description="Gmail message ID to reply to. This must be a valid Gmail message ID obtained from the fetch_emails_tool. If you're creating a new email (not replying), you can use any string like 'NEW_EMAIL'."
    )
    response_text: str = Field(
        description="Content of the reply"
    )
    email_address: str = Field(
        description="Current user's email address"
    )
    additional_recipients: Optional[List[str]] = Field(
        default=None,
        description="Optional additional recipients to include"
    )

# @tool(args_schema=SendEmailInput)
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

if __name__ == "__main__":
    #print(reply_email_tool(email_id="19d978bc63c7e61c", response_text="Hello, this is a test reply", additional_recipients=["randevilabq@outlook.com"]))
    print(send_new_email_tool(recipient="drrandys@yahoo.com", subject="Testing the send email tool again", body_text="Hello, this is a test email", additional_recipients=["randevilabq@outlook.com"]))

