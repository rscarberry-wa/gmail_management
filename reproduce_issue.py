
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from src.gmail_assistant.tools.gmail.gmail_manager import GmailManager


class TestGmailManagerReproduction(unittest.TestCase):

    @patch('src.gmail_assistant.gmail_manager.build')
    @patch('src.gmail_assistant.gmail_manager.Credentials')
    def test_get_emails_replied_to_missing(self, mock_creds, mock_build):
        # Setup mock service
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        # Mock messages.list
        # We have a thread with two messages:
        # 1. External -> Me (Original)
        # 2. Me -> External (Reply)
        mock_service.users().messages().list().execute.return_value = {
            'messages': [
                {'id': 'msg1', 'threadId': 'thread1'},
                {'id': 'msg2', 'threadId': 'thread1'}
            ]
        }

        # Mock messages.get for both messages
        msg1 = {
            'id': 'msg1',
            'threadId': 'thread1',
            'internalDate': str(int((datetime.now() - timedelta(minutes=10)).timestamp() * 1000)),
            'labelIds': ['INBOX', 'UNREAD'],
            'payload': {
                'headers': [
                    {'name': 'Message-ID', 'value': 'id1'},
                    {'name': 'Subject', 'value': 'Hello'},
                    {'name': 'From', 'value': 'external@example.com'},
                    {'name': 'To', 'value': 'me@example.com'},
                    {'name': 'Date', 'value': 'Thu, 16 Apr 2026 13:42:00 +0000'}
                ]
            },
            'snippet': 'Hi there'
        }
        msg2 = {
            'id': 'msg2',
            'threadId': 'thread1',
            'internalDate': str(int((datetime.now() - timedelta(minutes=5)).timestamp() * 1000)),
            'labelIds': ['SENT'],
            'payload': {
                'headers': [
                    {'name': 'Message-ID', 'value': 'id2'},
                    {'name': 'Subject', 'value': 'Re: Hello'},
                    {'name': 'From', 'value': 'me@example.com'},
                    {'name': 'To', 'value': 'external@example.com'},
                    {'name': 'Date', 'value': 'Thu, 16 Apr 2026 13:47:00 +0000'}
                ]
            },
            'snippet': 'I am replying'
        }

        def get_msg_side_effect(userId, id):
            if id == 'msg1': return msg1
            if id == 'msg2': return msg2
            return None

        mock_service.users().messages().get().execute.side_effect = get_msg_side_effect

        # Mock threads.get
        mock_service.users().threads().get().execute.return_value = {
            'id': 'thread1',
            'messages': [msg1, msg2]
        }

        # Mock users.getProfile
        mock_service.users().getProfile().execute.return_value = {
            'emailAddress': 'me@example.com'
        }

        manager = GmailManager()
        # When filter_latest=False, we expect BOTH messages to be returned 
        # (or at least msg1 even if it was replied to)
        emails = manager.get_emails(recipient="me", filter_latest=False, unread_only=False)

        print(f"Found {len(emails)} emails")
        for e in emails:
            print(f"ID: {e.id}, Subject: {e.subject}, Replied: {e.reply_to}")

        # The issue is that msg1 might be missing.
        # Let's check if msg1 is in emails
        msg_ids = [e.id for e in emails]
        self.assertIn('msg1', msg_ids, "msg1 should be in the returned list when filter_latest is False")
        self.assertIn('msg2', msg_ids, "msg2 should be in the returned list when filter_latest is False")

if __name__ == '__main__':
    unittest.main()
