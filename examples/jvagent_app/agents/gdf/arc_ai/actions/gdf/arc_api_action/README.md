# Arc API Action

Central API client and configuration action for Arc Incident Management System integration.

## Overview

The Resolv API Action provides a comprehensive HTTP client for interacting with the Resolv IMS platform. It manages authentication, handles retries, and provides high-level methods for all Resolv API operations including issues, contacts, groups, notices, and file uploads.

## Features

- **Persistent HTTP Session**: Reusable aiohttp session for improved performance
- **Automatic Authentication**: Handles API authentication headers automatically
- **Retry Logic**: Configurable retry attempts with exponential backoff
- **Comprehensive API Coverage**: Methods for all Resolv IMS endpoints
- **Contact Management**: Create, update, and lookup contacts
- **Issue Management**: Create, update, retrieve, and list issues
- **Group Subscriptions**: Subscribe/unsubscribe contacts to groups
- **File Uploads**: Upload media files from URLs
- **Project Integration**: Project and comment management
- **Legacy Compatibility**: Backward-compatible methods for older implementations

## Architecture

Inherits from `Action` base class and provides a centralized API client used by other Resolv actions (report interview, feedback interview, onboarding).

## Configuration

### Agent Configuration (agent.yaml)

```yaml
- action: gdf/arc_api_action
  context:
    enabled: true
    description: "Houses Arc API credentials and endpoints"
    timeout: 30
    retries: 3
```

### Configuration Properties

- **user_uuid**: User UUID for API authentication
- **secret_token**: Bearer token for API authorization
- **api_url**: Base URL for Resolv API
- **organization_slug**: Organization identifier
- **project_id**: Default project ID
- **agent_identifier**: Agent node identifier
- **timeout**: Request timeout in seconds (default: 30)
- **retries**: Number of retry attempts (default: 3)

## API Methods

### Issues API

#### create_issue

Create a new issue in the project.

```python
result = await resolv_api_action.create_issue(
    title="Pothole on Main Street",
    is_anonymous=False,
    description="Large pothole causing vehicle damage",
    original_description="User's original description",
    priority="high",
    category_id=28,
    reported_by_contact_id=123,
    reported_for_contact_id=456,
    ai_overview="AI-generated summary"
)
```

**Parameters:**

- `title` (str): Issue title
- `is_anonymous` (bool): Anonymous reporting flag
- `description` (str): Detailed description
- `original_description` (str): User's original input
- `priority` (str): Priority level (low, medium, high)
- `category_id` (int): Issue category ID
- `reported_by_contact_id` (int): Reporter contact ID
- `reported_for_contact_id` (int): Stakeholder contact ID
- `expected_resolution_date` (str): ISO format date
- `ai_overview` (str): AI-generated summary

**Returns:** Issue dictionary with ID and details

#### get_issue_by_id

Retrieve detailed information about a specific issue.

```python
issue = await resolv_api_action.get_issue_by_id(issue_id=223)
```

#### list_issues

List issues for the project with optional search query.

```python
issues = await resolv_api_action.list_issues(query="pothole")
```

#### update_issue

Update an existing issue.

```python
success = await resolv_api_action.update_issue(
    issue_id=223,
    status="in_progress",
    priority="high"
)
```

### Contacts API

#### get_contact_by_phone

Get or create a contact by phone number.

```python
contact = await resolv_api_action.get_contact_by_phone(
    phone="5926431530",
    name="John Smith",
    email="john@example.com"
)
```

**Returns:** Contact dictionary with ID and details

#### create_contact

Create a new contact.

```python
result = await resolv_api_action.create_contact(
    name="John Smith",
    phone="5926431530",
    email="john@example.com"
)
```

#### update_contact

Update an existing contact.

```python
success = await resolv_api_action.update_contact(
    contact_id=123,
    name="John Smith",
    phone="5926431530",
    email="john@example.com"
)
```

### Contact Groups API

#### get_contact_groups

Retrieve all contact groups for the organization.

```python
groups = await resolv_api_action.get_contact_groups(project_groups=True)
```

**Parameters:**

- `project_groups` (bool): Filter by current project's integration code

**Returns:** List of group dictionaries

#### subscribe_contact_to_group

Add a contact to a contact group.

```python
success = await resolv_api_action.subscribe_contact_to_group(
    contact_id=123,
    group_id=28
)
```

#### unsubscribe_contact_from_group

Remove a contact from a contact group.

```python
success = await resolv_api_action.unsubscribe_contact_from_group(
    contact_id=123,
    group_id=28
)
```

### File Upload API

#### upload_file_url

Upload a file from a URL and associate it with an entity.

```python
success = await resolv_api_action.upload_file_url(
    file_url="https://example.com/image.jpg",
    entity_id=223,
    entity_type="issue",
    file_type="attachment"
)
```

**Parameters:**

- `file_url` (str): URL to the file
- `entity_id` (int): Entity ID (issue, comment, etc.)
- `entity_type` (str): Entity type (default: "issue")
- `file_type` (str): File type (default: "attachment")

### Comments API

#### post_issue_comment

Post a comment on an issue.

```python
result = await resolv_api_action.post_issue_comment(
    project_id="32",
    issue_id="223",
    content="Work has been completed"
)
```

#### post_project_comment

Post a comment on a project.

```python
result = await resolv_api_action.post_project_comment(
    project_id="32",
    content="General project feedback"
)
```

### Projects API

#### get_projects

Get projects for the organization.

```python
projects = await resolv_api_action.get_projects(
    query="n:Agent:68a1556ce82af5547f4b4347"
)
```

#### get_project_id_by_agent_identifier

Get project ID by agent identifier (cached).

```python
project_id = await resolv_api_action.get_project_id_by_agent_identifier()
```

### Categories API

#### get_issue_categories

Retrieve all issue categories for the organization.

```python
categories = await resolv_api_action.get_issue_categories()
```

### Notices API

#### get_all_notices

Retrieve all notices for the organization.

```python
notices = await resolv_api_action.get_all_notices(status="sent")
```

#### get_notice

Retrieve details of a specific notice.

```python
notice = await resolv_api_action.get_notice(notice_id=123)
```

## High-Level Methods

### submit_report

Submit a complete report with contacts and attachments.

```python
result = await resolv_api_action.submit_report(
    title="Incident Report",
    is_anonymous=False,
    description="Generated description",
    original_description="User's description",
    attachments=["https://example.com/photo.jpg"],
    priority="high",
    category_id=28,
    reporting_on_behalf="yes",
    stakeholder_name="Jane Doe",
    stakeholder_address="456 Oak St",
    stakeholder_phone="5555555555",
    reporter_name="John Smith",
    reporter_phone="5926431530",
    reporter_address="123 Main St",
    ai_overview="AI summary"
)
```

**Features:**

- Automatically creates/retrieves contacts
- Uploads attachments
- Handles reporting on behalf logic
- Returns complete issue details

### submit_comment

Submit a comment to a project or issue with attachments.

```python
result = await resolv_api_action.submit_comment(
    content="Feedback content",
    report_id="223",
    attachments=["https://example.com/photo.jpg"]
)
```

### subscribe_user

Subscribe a user to a contact group by phone number.

```python
success = await resolv_api_action.subscribe_user(
    phone="5926431530",
    group_id=28,
    name="John Smith"
)
```

### unsubscribe_user

Unsubscribe a user from a contact group by phone number.

```python
success = await resolv_api_action.unsubscribe_user(
    phone="5926431530",
    group_id=28,
    name="John Smith"
)
```

### get_channels_page

Get a subscription channels page URL for a contact.

```python
url = await resolv_api_action.get_channels_page(
    phone_number="5926431530",
    name="John Smith"
)
```

## HTTP Request Method

### http_request

Low-level HTTP request method with automatic retries and authentication.

```python
result = await resolv_api_action.http_request(
    method="POST",
    url="https://api.example.com/endpoint",
    json_data={"key": "value"},
    timeout=30,
    retries=3
)
```

**Parameters:**

- `method` (str): HTTP method (GET, POST, PUT, DELETE)
- `url` (str): Full URL
- `params` (dict): Query parameters
- `json_data` (any): JSON payload
- `data` (dict/str/bytes): Form data
- `headers` (dict): Custom headers (overrides defaults)
- `timeout` (int): Request timeout
- `retries` (int): Retry attempts

**Returns:** Dictionary with `status`, `json`, and `text` keys

## Lifecycle Hooks

### on_enable

Initializes the persistent HTTP session when action is enabled.

### on_disable

Closes the persistent HTTP session when action is disabled.

## API Endpoints

The action provides standard API endpoints via `endpoints.py`:

- `GET /actions/{action_id}/status` - Get action status
- `GET /actions/{action_id}/config` - Get configuration
- `POST /actions/{action_id}/issues` - Create issue
- `GET /actions/{action_id}/issues/{issue_id}` - Get issue
- `POST /actions/{action_id}/contacts` - Create contact
- `GET /actions/{action_id}/groups` - List groups

## Error Handling

- Automatic retries with exponential backoff
- Comprehensive error logging
- Graceful fallbacks for failed requests
- HTTP status code validation

## Usage Example

```python
# Get the action instance
resolv_api = await action.get_action("ResolvAPIAction")

# Create a contact
contact = await resolv_api.get_contact_by_phone(
    phone="5926431530",
    name="John Smith"
)

# Create an issue
issue = await resolv_api.create_issue(
    title="Pothole Report",
    description="Large pothole on Main Street",
    priority="high",
    category_id=28,
    reported_by_contact_id=contact['id']
)

# Upload media
await resolv_api.upload_file_url(
    file_url="https://example.com/photo.jpg",
    entity_id=issue['id'],
    entity_type="issue"
)
```

## Dependencies

- `aiohttp` - Async HTTP client
- `asyncio` - Async runtime

## File Structure

```
resolv_api_action/
├── __init__.py              # Package initialization
├── resolv_api_action.py     # Main action implementation
├── endpoints.py             # API endpoints
├── info.yaml                # Action metadata
└── README.md                # This file
```

## Legacy Compatibility

The action maintains backward compatibility with older method names:

- `get_subscription_groups()` → `get_contact_groups()`
- `get_issue()` → `get_issue_by_id()` or `list_issues()`
- `get_alert()` → `get_all_notices()`
- `cancel_incident()` → `update_issue(status="closed")`
- `get_categories()` → `get_issue_categories()`

## Security

- API credentials stored in agent.yaml
- Bearer token authentication
- HTTPS-only communication
- Sensitive data not logged

## Testing

Test scenarios:

- Contact creation and lookup
- Issue creation with all fields
- File upload from URLs
- Group subscription/unsubscription
- Comment posting
- Error handling and retries
- Session persistence
