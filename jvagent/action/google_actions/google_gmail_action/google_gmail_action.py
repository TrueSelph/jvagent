import logging
import base64
from email.mime.text import MIMEText
from typing import Any, ClassVar, Dict, List, Optional

from ..google_action import GoogleAction

logger = logging.getLogger(__name__)

class GoogleGmailAction(GoogleAction):
    """Action for Google Gmail operations using a service account."""

    API_SERVICE_NAME: ClassVar[str] = 'gmail'
    API_VERSION: ClassVar[str] = 'v1'
    SCOPES: ClassVar[List[str]] = [
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/gmail.modify'
    ]

    async def send_email(
        self, 
        to: str, 
        subject: str, 
        body: str, 
        user_id: str = 'me'
    ) -> Dict[str, Any]:
        """Send an email via Gmail API."""
        service = await self.get_service()
        
        message = MIMEText(body)
        message['to'] = to
        message['subject'] = subject
        
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return service.users().messages().send(userId=user_id, body={'raw': raw}).execute()

    async def list_messages(self, query: str = '', max_results: int = 10, user_id: str = 'me') -> List[Dict[str, Any]]:
        """List messages in Gmail inbox."""
        service = await self.get_service()
        results = service.users().messages().list(userId=user_id, q=query, maxResults=max_results).execute()
        return results.get('messages', [])

    async def get_profile(self, user_id: str = 'me') -> Dict[str, Any]:
        """Get user Gmail profile."""
        service = await self.get_service()
        return service.users().getProfile(userId=user_id).execute()
