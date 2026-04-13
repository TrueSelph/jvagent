"""Resolv API Action for interacting with the Resolv platform.

Provides methods for managing contacts, issues, notices, and project communications.
"""
import time
import json
import logging
from typing import Any, Dict, List, Optional, Union

import aiohttp
import asyncio
from jvagent.action.base import Action
from jvspatial.core.annotations import attribute

logger = logging.getLogger(__name__)


class ResolvAPIAction(Action):
    """Resolv API Action for interacting with the Resolv platform."""

    user_uuid: str = attribute(default="", description="User UUID")
    secret_token: str = attribute(default="", description="Secret Token")
    api_url: str = attribute(default="", description="API URL")

    organization_slug: str = attribute(default="", description="Organization Slug")
    project_id: str = attribute(default="", description="Project ID")
    agent_identifier: str = attribute(default="", description="Agent Identifier")

    timeout: int = attribute(default=30, description="Operation timeout in seconds", ge=1)
    retries: int = attribute(default=3, description="Number of retry attempts", ge=0, le=10)

    _session: Optional[aiohttp.ClientSession] = None

    async def on_enable(self) -> None:
        """Initialize the persistent HTTP session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(
                timeout=timeout
            )
        await super().on_enable()

    async def on_disable(self) -> None:
        """Close the persistent HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        await super().on_disable()

    def get_agent_identifier(self) -> str:
        """Get agent identifier, lazily fetching if needed."""
        if not self.agent_identifier:
            agent = self.get_agent()
            if agent:
                self.agent_identifier = agent.id if hasattr(agent, 'id') else agent.get('id', '')
        return self.agent_identifier

    def _get_headers(self) -> Dict[str, str]:
        """Get common headers for API requests."""
        return {
            'Content-Type': 'application/json',
            'X-User-Uuid': self.user_uuid,
            'Authorization': f"Bearer {self.secret_token}",
            'Accept': 'application/json'
        }

    def get_curl_command(self, method, url, params, headers, json_data, data):
        parts = [f"curl -X {method} \\"]

        if headers:
            for k, v in headers.items():
                parts.append(f"  -H '{k}: {v}' \\")

        if json_data is not None:
            parts.append("  -H 'Content-Type: application/json' \\")
            parts.append(f"  -d '{json.dumps(json_data)}' \\")

        elif data is not None:
            parts.append(f"  -d '{data}' \\")

        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"

        parts.append(f"  '{url}'")

        return "\n".join(parts)


    async def http_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Any] = None,
        data: Optional[Union[Dict[str, Any], str, bytes]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
        show_curl_for_function: str = "",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Perform HTTP request to the Resolv API with automatic auth headers and retries.
        """
        method = method.upper()
        if method not in ('GET', 'POST', 'PUT', 'DELETE'):
            raise ValueError(f"Method {method} not supported")

        effective_timeout = timeout if timeout is not None else self.timeout
        effective_retries = retries if retries is not None else self.retries

        # Determine if we can use the persistent session
        use_persistent = self._session is not None and not self._session.closed
        session = self._session if use_persistent else None



        # If no persistent session, create a temporary one for this request
        if not use_persistent:
            timeout_obj = aiohttp.ClientTimeout(total=effective_timeout)
            session = aiohttp.ClientSession(
                timeout=timeout_obj
            )


        last_exception = None
        timeout_obj = aiohttp.ClientTimeout(total=effective_timeout)

        # Determine effective headers for this specific request
        # If headers are provided, use them (caller responsible for full set/auth)
        # Otherwise, use defaults.
        request_headers = headers if headers is not None else self._get_headers()
        if show_curl_for_function:
            curl_command = self.get_curl_command(method, url, params, request_headers, json_data, data)
            logger.warning(f"CURL for {show_curl_for_function}:\n\n{curl_command}\n\n")

        try:
            for attempt in range(effective_retries + 1):
                try:

                    async with session.request(
                        method,
                        url,
                        params=params,
                        json=json_data,
                        data=data,
                        headers=request_headers,
                        timeout=timeout_obj,
                        **kwargs
                    ) as response:
                        status = response.status
                        text = await response.text()
                        try:
                            json_res = await response.json()
                        except (aiohttp.ContentTypeError, json.JSONDecodeError):
                            json_res = None

                        return {'status': status, 'json': json_res, 'text': text}

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_exception = e
                    if attempt == effective_retries:
                        logger.error(f"Request failed after {attempt + 1} attempts: {method} {url} - {e}")
                        break

                    wait_time = 0.5 * (2 ** attempt)
                    await asyncio.sleep(wait_time)
        finally:
            if not use_persistent and session:
                await session.close()

        if last_exception:
            raise Exception(f"Request failed: {url}") from last_exception
        raise Exception(f"Request failed: {url} (Unknown error)")

    # Contact Groups API

    async def get_contact_groups(self, project_groups: bool = True) -> List[Dict[str, Any]]:
        """
        Retrieve all contact groups for the organization.

        Args:
            project_groups: If True, filters groups by the current project's integration code.

        Returns:
            A list of contact group dictionaries.
        """
        base_endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/contact-groups"

        if project_groups:
            agent_id = self.get_agent_identifier()
            projects = await self.get_projects(agent_id)
            if projects and 'integrationCode' in projects[0]:
                endpoint = f"{base_endpoint}?integrationCode={projects[0]['integrationCode']}"
            else:
                endpoint = base_endpoint
        else:
            endpoint = base_endpoint

        try:
            res = await self.http_request('GET', endpoint)

            if res['status'] == 200:
                data = res['json']
                return data.get('groups', [])

            logger.error(f"Failed to get contact groups. Status: {res['status']}, Response: {res['text']}")
            return []
        except Exception as e:
            logger.error(f"Error getting contact groups: {e}")
            return []

    async def subscribe_contact_to_group(self, contact_id: int, group_id: int) -> bool:
        """
        Add a contact to a contact group.

        Args:
            contact_id: The ID of the contact to subscribe.
            group_id: The ID of the group to subscribe the contact to.

        Returns:
            True if successful, False otherwise.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/contact-groups/{group_id}/subscribe"
        payload = {"contactId": contact_id}

        try:
            res = await self.http_request('POST', endpoint, json_data=payload)
            if res['status'] in [200, 201]:
                return True
            elif res['status'] == 400:
                return True

            logger.error(f"Failed to subscribe contact to group. Status: {res['status']}, Response: {res['text']}")
            return False
        except Exception as e:
            logger.error(f"Error subscribing contact to group: {e}")
            return False

    async def unsubscribe_contact_from_group(self, contact_id: int, group_id: int) -> bool:
        """
        Remove a contact from a contact group.

        Args:
            contact_id: The ID of the contact to unsubscribe.
            group_id: The ID of the group to unsubscribe the contact from.

        Returns:
            True if successful, False otherwise.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/contact-groups/{group_id}/unsubscribe"
        payload = {"contactId": contact_id}

        try:
            res = await self.http_request('POST', endpoint, json_data=payload)
            if res['status'] in [200, 201]:
                return True

            logger.error(f"Failed to unsubscribe contact from group. Status: {res['status']}, Response: {res['text']}")
            return False
        except Exception as e:
            logger.error(f"Error unsubscribing contact from group: {e}")
            return False

    # Issues API

    async def create_issue(
        self,
        is_anonymous: bool = False,
        description: Optional[str] = None,
        reported_by_contact_id: Optional[int] = None,
        reported_for_contact_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Create a new issue in the project.

        Args:
            is_anonymous: Whether the reporter wants to remain anonymous.
            description: Detailed description of the issue.
            reported_by_contact_id: ID of the contact who reported the issue.
            reported_for_contact_id: ID of the contact for whom the issue is reported.

        Returns:
            The created issue dictionary.
        """
        project_id = await self.get_project_id_by_agent_identifier()
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/projects/{project_id}/issues"

        payload = {"isAnonymous": is_anonymous}

        if description:
            payload["description"] = description
        if reported_by_contact_id:
            payload["reportedByContactId"] = int(reported_by_contact_id)
        if reported_for_contact_id:
            payload["reportedForContactId"] = int(reported_for_contact_id)

        try:
            res = await self.http_request('POST', endpoint, json_data=payload, show_curl_for_function="")
            if res['status'] in [200, 201]:
                return res['json'].get('issue', {})

            logger.error(f"Failed to create issue. Status: {res['status']}, Response: {res['text']}")
            return {}
        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return {}

    async def upload_file_url(
        self,
        file_url: str,
        entity_id: int,
        entity_type: str = "issue",
        file_type: str = "attachment"
    ) -> bool:
        """
        Upload a file from a URL and associate it with an entity.

        Args:
            file_url: URL to the file.
            entity_id: ID of the entity (e.g., issue ID).
            entity_type: Type of the entity (e.g., "issue").
            file_type: Type of the file (e.g., "attachment").

        Returns:
            True if successful, False otherwise.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/files/upload"

        data = {
            "entityType": entity_type,
            "entityId": entity_id,
            "fileType": file_type,
            "fileUrl": file_url
        }

        headers = self._get_headers()
        headers.pop("Content-Type", None)

        try:
            res = await self.http_request('POST', endpoint, data=data, headers=headers)
            if res['status'] == 200:
                return True

            logger.error(f"Failed to upload file. Status: {res['status']}, Response: {res['text']}")
            return False
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            return False


    async def update_issue(
        self,
        issue_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
        category_id: Optional[int] = None,
        expected_resolution_date: Optional[str] = None
    ) -> bool:
        """
        Update an existing issue.

        Args:
            issue_id: The ID of the issue to update.
            title: New title.
            description: New description.
            priority: New priority level.
            status: New status level.
            category_id: New category ID.
            expected_resolution_date: New expected resolution date.

        Returns:
            True if successful, False otherwise.
        """
        project_id = await self.get_project_id_by_agent_identifier()
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/projects/{project_id}/issues/{issue_id}"

        payload = {}
        if title is not None: payload["title"] = title
        if description is not None: payload["description"] = description
        if priority is not None: payload["priority"] = priority
        if status is not None: payload["status"] = status
        if category_id is not None: payload["categoryId"] = category_id
        if expected_resolution_date is not None: payload["expectedResolutionDate"] = expected_resolution_date

        try:
            res = await self.http_request('PUT', endpoint, json_data=payload)
            if res['status'] in [200, 201]:
                return True

            logger.error(f"Failed to update issue. Status: {res['status']}, Response: {res['text']}")
            return False
        except Exception as e:
            logger.error(f"Error updating issue: {e}")
            return False

    async def get_issue_by_id(self, issue_id: Union[str, int]) -> Dict[str, Any]:
        """
        Retrieve detailed information about a specific issue.

        Args:
            issue_id: The ID of the issue to retrieve.

        Returns:
            The issue dictionary.
        """
        project_id = await self.get_project_id_by_agent_identifier()
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/projects/{project_id}/issues/{issue_id}"

        try:
            res = await self.http_request('GET', endpoint)
            if res['status'] == 200:
                return res['json'].get('issue', {})

            logger.error(f"Failed to get issue {issue_id}. Status: {res['status']}, Response: {res['text']}")
            return {}
        except Exception as e:
            logger.error(f"Error getting issue {issue_id}: {e}")
            return {}

    async def list_issues(self, query: str = "") -> List[Dict[str, Any]]:
        """
        List issues for the project, optionally filtered by a query.

        Args:
            query: Search query to filter issues.

        Returns:
            A list of issue dictionaries.
        """
        project_id = await self.get_project_id_by_agent_identifier()
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/projects/{project_id}/issues"
        params = {"query": query} if query else {}

        try:
            res = await self.http_request('GET', endpoint, params=params)
            if res['status'] == 200:
                return res['json'].get('issues', [])

            logger.error(f"Failed to list issues. Status: {res['status']}, Response: {res['text']}")
            return []
        except Exception as e:
            logger.error(f"Error listing issues: {e}")
            return []

    # Backwards compatibility for get_issue
    async def get_issue(self, issue_id: str = "", query: str = "") -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Get issue(s) by ID or query (legacy method).

        Args:
            issue_id: Specific issue ID to retrieve.
            query: Search query to filter issues.

        Returns:
            Single issue dict if issue_id provided, otherwise list of issues.
        """
        if issue_id:
            return await self.get_issue_by_id(issue_id)
        return await self.list_issues(query)

    # Issue Categories API

    async def get_issue_categories(self) -> List[Dict[str, Any]]:
        """
        Retrieve all issue categories for the organization.

        Returns:
            A list of category dictionaries.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/issue-categories"

        try:
            res = await self.http_request('GET', endpoint)
            if res['status'] == 200:
                return res['json'].get('categories', [])

            logger.error(f"Failed to get issue categories. Status: {res['status']}, Response: {res['text']}")
            return []
        except Exception as e:
            logger.error(f"Error getting issue categories: {e}")
            return []

    # Contacts API

    async def get_contact_by_phone(self, phone: str, name: str = "", email: str = "") -> Dict[str, Any]:
        """
        Get or create a contact by phone number.

        Args:
            phone: Phone number to search for.
            name: Name to use if contact needs to be created.
            email: Email to use if contact needs to be created.

        Returns:
            The contact dictionary.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/contacts/lookup?phone={phone}"

        try:
            res = await self.http_request('GET', endpoint)

            if res['status'] == 200:
                return res['json'].get('contact', {})
            elif res['status'] == 404:
                create_result = await self.create_contact(phone=phone, name=name or f"User_{phone}", email=email)
                return create_result.get('contact', {})

            logger.error(f"Failed to get contact by phone. Status: {res['status']}, Response: {res['text']}")
            return {}
        except Exception as e:
            logger.error(f"Error getting contact by phone: {e}")
            return []

    async def update_contact(self, contact_id: int, name: str, phone: str, email: str) -> bool:
        """
        Update an existing contact.

        Args:
            contact_id: The ID of the contact to update.
            name: New name.
            phone: New phone number.
            email: New email.

        Returns:
            True if successful, False otherwise.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/contacts/{contact_id}"
        project_id = await self.get_project_id_by_agent_identifier()
        data = {
            "name": name,
            "phone": phone,
            "email": email,
            "projectId": project_id
        }

        try:
            res = await self.http_request('PUT', endpoint, json_data=data)
            if res['status'] == 200:
                return True

            logger.error(f"Failed to update contact. Status: {res['status']}, Response: {res['text']}")
            return False
        except Exception as e:
            logger.error(f"Error updating contact: {e}")
            return False

    async def create_contact(self, name: str, phone: str, email: str) -> Dict[str, Any]:
        """
        Create a new contact.

        Args:
            name: Contact name.
            phone: Contact phone number.
            email: Contact email.

        Returns:
            The created contact response dictionary.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/contacts"
        project_id = await self.get_project_id_by_agent_identifier()
        data = {
            "name": name,
            "phone": phone,
            "email": email,
            "projectId": project_id
        }

        try:
            res = await self.http_request('POST', endpoint, json_data=data)
            if res['status'] == 201:
                return res['json']

            logger.error(f"Failed to create contact. Status: {res['status']}, Response: {res['text']}")
            return {}
        except Exception as e:
            logger.error(f"Error creating contact: {e}")
            return {}

    # Notices API

    async def get_notice(self, notice_id: int) -> Dict[str, Any]:
        """
        Retrieve details of a specific notice.

        Args:
            notice_id: The ID of the notice to retrieve.

        Returns:
            The notice dictionary.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/notices/{notice_id}"

        try:
            res = await self.http_request('GET', endpoint)
            if res['status'] == 200:
                return res['json']

            logger.error(f"Failed to get notice. Status: {res['status']}, Response: {res['text']}")
            return {}
        except Exception as e:
            logger.error(f"Error getting notice: {e}")
            return {}

    async def get_all_notices(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieve all notices for the organization.

        Args:
            status: Optional filter by status (e.g., "sent", "draft").

        Returns:
            A list of notice dictionaries.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/notices"
        params = {"agentIdentifier": self.get_agent_identifier()}
        if status:
            params["status"] = status

        try:
            res = await self.http_request('GET', endpoint, params=params)
            if res['status'] == 200:
                return res['json'].get('notices', [])

            logger.error(f"Failed to get notices. Status: {res['status']}, Response: {res['text']}")
            return []
        except Exception as e:
            logger.error(f"Error getting notices: {e}")
            return []

    # Legacy method compatibility

    async def get_subscription_groups(self) -> List[Dict[str, Any]]:
        """
        Get contact groups (legacy method).

        Returns:
            A list of contact group dictionaries.
        """
        return await self.get_contact_groups()

    async def submit_report(
        self,
        is_anonymous: bool,
        original_description: str,
        attachments: List[str],
        reporting_on_behalf: str,
        stakeholder_name: str,
        stakeholder_phone: str,
        reporter_name: str,
        reporter_phone: str
    ) -> Dict[str, Any]:
        """
        Submit a report (legacy method).

        Args:
            is_anonymous: Whether the report is anonymous.
            original_description: Original description of the report.
            attachments: List of attachment URLs.
            reporting_on_behalf: Whether reporting on behalf of someone else ("yes"/"no").
            stakeholder_name: Name of the stakeholder.
            stakeholder_phone: Phone number of the stakeholder.
            reporter_name: Name of the reporter.
            reporter_phone: Phone number of the reporter.

        Returns:
            The created issue dictionary.
        """
        reported_for_contact_id = None
        reported_by_contact_id = None

        if str(reporting_on_behalf).lower() == "yes":
            reported_for_contact = await self.get_contact_by_phone(phone=stakeholder_phone, name=stakeholder_name)
            if reported_for_contact:
                reported_for_contact_id = reported_for_contact.get('id')

            reported_by_contact = await self.get_contact_by_phone(phone=reporter_phone, name=reporter_name)
            if reported_by_contact:
                reported_by_contact_id = reported_by_contact.get('id')
        else:
            reported_by_contact = await self.get_contact_by_phone(phone=reporter_phone, name=reporter_name)
            if reported_by_contact:
                reported_by_contact_id = reported_by_contact.get('id')

        result = await self.create_issue(
            description=original_description,
            reported_by_contact_id=reported_by_contact_id,
            reported_for_contact_id=reported_for_contact_id,
            is_anonymous=is_anonymous
        )

        if attachments and result and 'id' in result:
            for attachment_url in attachments:
                await self.upload_file_url(
                    file_url=attachment_url,
                    entity_id=result['id'],
                    file_type="attachment",
                    entity_type="issue"
                )
            return result
        return {}

    async def subscribe_user(self, phone: str, group_id: int, name: str = "user") -> bool:
        """
        Subscribe a user to a contact group by phone number.

        Args:
            phone: User's phone number.
            group_id: ID of the contact group.
            name: User's name (default: "user").

        Returns:
            True if successful, False otherwise.
        """
        contact = await self.get_contact_by_phone(phone=phone, name=f"{name}_{phone}")
        if contact and 'id' in contact:
            return await self.subscribe_contact_to_group(contact_id=contact['id'], group_id=group_id)

        logger.warning(f"Failed to subscribe user {phone}: contact not found or created")
        return False

    async def unsubscribe_user(self, phone: str, group_id: int, name: str = "user") -> bool:
        """
        Unsubscribe a user from a contact group by phone number.

        Args:
            phone: User's phone number.
            group_id: ID of the contact group.
            name: User's name (default: "user").

        Returns:
            True if successful, False otherwise.
        """
        contact = await self.get_contact_by_phone(phone=phone, name=f"{name}_{phone}")
        if contact and 'id' in contact:
            return await self.unsubscribe_contact_from_group(contact_id=contact['id'], group_id=group_id)

        logger.warning(f"Failed to unsubscribe user {phone}: contact not found")
        return False

    async def get_alert(self) -> Dict[str, Any]:
        """
        Get the latest sent notice (legacy method).

        Returns:
            The most recent notice dictionary, or empty dict if none found.
        """
        notices = await self.get_all_notices(status="sent")
        return notices[0] if notices else {}

    async def cancel_incident(self, reference_number: str) -> bool:
        """
        Close an issue by reference number (legacy method).

        Args:
            reference_number: The issue ID as a string.

        Returns:
            True if successful, False otherwise.
        """
        try:
            return await self.update_issue(issue_id=int(reference_number), status="closed")
        except (ValueError, TypeError, Exception) as e:
            logger.error(f"Error canceling incident {reference_number}: {e}")
            return False

    async def get_categories(self) -> List[Dict[str, Any]]:
        """
        Get issue categories (legacy method).

        Returns:
            A list of category dictionaries.
        """
        return await self.get_issue_categories()

    # Communication & Projects

    async def post_project_comment(self, project_id: str, content: str) -> Dict[str, Any]:
        """
        Post a comment on a project.

        Args:
            project_id: The ID of the project.
            content: The comment content.

        Returns:
            The comment response dictionary.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/projects/{project_id}/comments"
        payload = {"content": content, "type": "feedback"}

        try:
            res = await self.http_request('POST', endpoint, json_data=payload, show_curl_for_function="")
            if res['status'] in [200, 201]:
                return res['json']['comment']
            else:
                raise Exception(f"HTTP Error {res['status']}: {res.get('json', {})}")
        except Exception as e:
            logger.error(f"Error posting project comment: {e}")
            return {}

    async def post_issue_comment(self, project_id: str, issue_id: str, content: str) -> Dict[str, Any]:
        """
        Post a comment on an issue.

        Args:
            project_id: The ID of the project.
            issue_id: The ID of the issue.
            content: The comment content.

        Returns:
            The comment response dictionary.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/projects/{project_id}/issues/{issue_id}/comments"
        payload = {"content": content, "type": "feedback"}

        try:
            res = await self.http_request('POST', endpoint, json_data=payload)
            if res['status'] in [200, 201]:
                return res['json']['comment']
            else:
                raise Exception(f"HTTP Error {res['status']}: {res.get('json', {})}")
        except Exception as e:
            logger.error(f"Error posting issue comment: {e}")
            return {}

    async def get_projects(self, query: str = "") -> List[Dict[str, Any]]:
        """
        Get projects for the organization.

        Args:
            query: Optional agent identifier to filter projects.

        Returns:
            A list of project dictionaries.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/projects"
        if query:
            endpoint = f"{endpoint}?agentIdentifier={query}"

        try:
            res = await self.http_request('GET', endpoint)
            if res['status'] == 200:
                return res['json'].get('projects', [])

            logger.error(f"Failed to get projects. Status: {res['status']}, Response: {res['text']}")
            return []
        except Exception as e:
            logger.error(f"Error getting projects: {e}")
            return []

    async def get_project_id_by_agent_identifier(self) -> str:
        """
        Get project ID by agent identifier, caching it once found.

        Returns:
            The project ID as a string.
        """
        if not self.project_id:
            agent_id = self.get_agent_identifier()
            projects = await self.get_projects(agent_id)
            if projects:
                self.project_id = str(projects[0].get('id', ''))
        return self.project_id

    async def get_issues_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve all issues for a specific project.

        Args:
            project_id: The ID of the project.

        Returns:
            A list of issue dictionaries.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/issues"
        params = {"projectId": project_id}

        try:
            res = await self.http_request('GET', endpoint, params=params)
            if res['status'] == 200:
                return res['json'].get('issues', [])

            logger.error(f"Failed to get issues by project. Status: {res['status']}, Response: {res['text']}")
            return []
        except Exception as e:
            logger.error(f"Error getting issues by project: {e}")
            return []

    async def get_channels_page(self, phone_number: str, name: str) -> str:
        """
        Get a subscription channels page URL for a contact.

        Args:
            phone_number: Contact phone number.
            name: Contact name.

        Returns:
            The subscription page URL.
        """
        endpoint = f"{self.api_url.rstrip('/')}/organizations/{self.organization_slug}/contacts/subscription-link"
        payload = {"phone": phone_number, "name": name}

        try:
            res = await self.http_request('POST', endpoint, json_data=payload)
            if res['status'] == 200:
                return res['json'].get('url', '')

            logger.error(f"Failed to get channels page. Status: {res['status']}, Response: {res['text']}")
            return ""
        except Exception as e:
            logger.error(f"Error getting channels page: {e}")
            return ""

    async def submit_comment(
        self,
        content: str,
        report_id: str = "",
        attachments: list = []
    ) -> Dict[str, Any]:
        """
        Submit a comment to a project or issue.

        Args:
            content: The comment content.
            report_id: Optional issue ID to comment on.
            attachments: Optional list of attachment URLs.

        Returns:
            The comment response dictionary.
        """

        project_id = await self.get_project_id_by_agent_identifier()

        if report_id:
            result = await self.post_issue_comment(project_id, report_id, content)
        else:
            result = await self.post_project_comment(project_id, content)

        if attachments and result and 'id' in result:
            for attachment_url in attachments:
                await self.upload_file_url(
                    file_url=attachment_url,
                    entity_id=result.get('id', ''),
                    file_type="attachment",
                    entity_type="comment"
                )

        return result


