"""Tests for WhatsApp webhook URL generation with API key authentication."""

import pytest

pytest.importorskip("filetype")

from unittest.mock import AsyncMock, MagicMock, patch

from jvspatial.api.auth.models import APIKey
from jvspatial.core.context import GraphContext

from jvagent.action.whatsapp.whatsapp_action import WhatsAppAction
from jvagent.core.agent import Agent


@pytest.fixture
def mock_agent():
    """Create a mock agent for testing."""
    agent = MagicMock(spec=Agent)
    agent.id = "n.Agent.test123"
    agent.name = "TestAgent"
    return agent


@pytest.fixture
def whatsapp_action(mock_agent):
    """Create a WhatsAppAction instance for testing."""
    action = WhatsAppAction(
        id="n.WhatsAppAction.test456",
        base_url="http://localhost:8000",
        provider="wppconnect",
    )

    # Patch get_agent and save at class level (instance patch fails with jvspatial Node)
    with patch.object(
        WhatsAppAction, "get_agent", new_callable=AsyncMock, return_value=mock_agent
    ), patch.object(WhatsAppAction, "save", new_callable=AsyncMock):
        yield action


@pytest.mark.asyncio
async def test_get_webhook_url_generates_new_key(whatsapp_action, mock_agent):
    """Test that get_webhook_url generates a new API key when none exists."""
    with patch(
        "jvagent.action.whatsapp.whatsapp_action.get_or_create_system_user"
    ) as mock_user, patch(
        "jvagent.action.whatsapp.whatsapp_action.APIKeyService"
    ) as mock_service_class, patch(
        "jvagent.action.whatsapp.whatsapp_action.get_prime_database"
    ) as mock_db, patch(
        "jvagent.action.whatsapp.whatsapp_action.GraphContext"
    ) as mock_context_class:

        # Setup mocks
        mock_user.return_value = "o.User.system123"
        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance

        mock_context = MagicMock(spec=GraphContext)
        mock_context_class.return_value = mock_context

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        # Mock API key generation
        mock_api_key = MagicMock(spec=APIKey)
        mock_api_key.id = "o.APIKey.key123"
        mock_api_key.key_prefix = "test_mock_"

        plaintext_key = "test_mock_api_key_12345"
        mock_service.generate_key = AsyncMock(
            return_value=(plaintext_key, mock_api_key)
        )

        # Call get_webhook_url
        webhook_url = await whatsapp_action.get_webhook_url()

        # Verify results
        assert webhook_url is not None
        assert webhook_url.startswith(
            "http://localhost:8000/api/whatsapp/interact/webhook/n.Agent.test123"
        )
        assert "?api_key=" in webhook_url
        assert plaintext_key in webhook_url

        # Verify API key was generated
        mock_service.generate_key.assert_called_once()
        call_kwargs = mock_service.generate_key.call_args[1]
        assert call_kwargs["user_id"] == "o.User.system123"
        assert call_kwargs["name"] == "WhatsApp Webhook - TestAgent"
        assert call_kwargs["permissions"] == ["webhook:whatsapp"]
        assert call_kwargs["expires_in_days"] is None
        assert call_kwargs["allowed_ips"] == []
        assert call_kwargs["allowed_endpoints"] == ["/api/whatsapp/interact/webhook/*"]

        # Verify action was updated
        assert whatsapp_action.webhook_api_key_id == "o.APIKey.key123"
        assert whatsapp_action.webhook_url == webhook_url
        whatsapp_action.save.assert_called_once()


@pytest.mark.asyncio
async def test_get_webhook_url_with_ip_restriction(whatsapp_action, mock_agent):
    """Test that get_webhook_url respects IP whitelisting."""
    with patch(
        "jvagent.action.whatsapp.whatsapp_action.get_or_create_system_user"
    ) as mock_user, patch(
        "jvagent.action.whatsapp.whatsapp_action.APIKeyService"
    ) as mock_service_class, patch(
        "jvagent.action.whatsapp.whatsapp_action.get_prime_database"
    ) as mock_db, patch(
        "jvagent.action.whatsapp.whatsapp_action.GraphContext"
    ) as mock_context_class:

        # Setup mocks
        mock_user.return_value = "o.User.system123"
        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance

        mock_context = MagicMock(spec=GraphContext)
        mock_context_class.return_value = mock_context

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        # Mock API key generation
        mock_api_key = MagicMock(spec=APIKey)
        mock_api_key.id = "o.APIKey.key123"
        mock_api_key.key_prefix = "test_mock_"

        plaintext_key = "test_mock_api_key_12345"
        mock_service.generate_key = AsyncMock(
            return_value=(plaintext_key, mock_api_key)
        )

        # Call get_webhook_url with IP restriction
        allowed_ip = "203.0.113.0"
        webhook_url = await whatsapp_action.get_webhook_url(allowed_ip=allowed_ip)

        # Verify IP restriction was applied
        call_kwargs = mock_service.generate_key.call_args[1]
        assert call_kwargs["allowed_ips"] == [allowed_ip]


@pytest.mark.asyncio
async def test_get_webhook_url_reuses_existing_url(whatsapp_action, mock_agent):
    """Test that get_webhook_url reuses existing URL when appropriate."""
    # Set up existing webhook URL
    existing_url = "http://localhost:8000/api/whatsapp/interact/webhook/n.Agent.test123?api_key=test_mock_existing"
    whatsapp_action.webhook_url = existing_url
    whatsapp_action.webhook_api_key_id = "o.APIKey.existing123"

    with patch(
        "jvagent.action.whatsapp.whatsapp_action.get_or_create_system_user"
    ) as mock_user, patch(
        "jvagent.action.whatsapp.whatsapp_action.get_prime_database"
    ) as mock_db, patch(
        "jvagent.action.whatsapp.whatsapp_action.GraphContext"
    ) as mock_context_class:

        # Setup mocks
        mock_user.return_value = "o.User.system123"
        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance

        mock_context = MagicMock(spec=GraphContext)
        mock_context_class.return_value = mock_context

        # Mock existing API key
        mock_existing_key = MagicMock(spec=APIKey)
        mock_existing_key.id = "o.APIKey.existing123"
        mock_existing_key.is_active = True
        mock_existing_key.allowed_ips = []  # No IP restriction

        mock_context.get = AsyncMock(return_value=mock_existing_key)

        # Call get_webhook_url without regenerate
        webhook_url = await whatsapp_action.get_webhook_url(regenerate=False)

        # Verify existing URL was reused
        assert webhook_url == existing_url
        # Should not have called save (no changes)
        whatsapp_action.save.assert_not_called()


@pytest.mark.asyncio
async def test_get_webhook_url_regenerates_on_request(whatsapp_action, mock_agent):
    """Test that get_webhook_url regenerates API key when regenerate=True."""
    # Set up existing webhook URL
    whatsapp_action.webhook_url = "http://localhost:8000/api/whatsapp/interact/webhook/n.Agent.test123?api_key=test_mock_old"
    whatsapp_action.webhook_api_key_id = "o.APIKey.old123"

    with patch(
        "jvagent.action.whatsapp.whatsapp_action.get_or_create_system_user"
    ) as mock_user, patch(
        "jvagent.action.whatsapp.whatsapp_action.APIKeyService"
    ) as mock_service_class, patch(
        "jvagent.action.whatsapp.whatsapp_action.get_prime_database"
    ) as mock_db, patch(
        "jvagent.action.whatsapp.whatsapp_action.GraphContext"
    ) as mock_context_class:

        # Setup mocks
        mock_user.return_value = "o.User.system123"
        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance

        mock_context = MagicMock(spec=GraphContext)
        mock_context_class.return_value = mock_context

        # Mock old API key (for revocation)
        mock_old_key = MagicMock(spec=APIKey)
        mock_old_key.id = "o.APIKey.old123"
        mock_old_key.is_active = True

        # Mock new API key generation
        mock_new_key = MagicMock(spec=APIKey)
        mock_new_key.id = "o.APIKey.new123"
        mock_new_key.key_prefix = "test_mock_new_"

        mock_context.get = AsyncMock(return_value=mock_old_key)
        mock_context.save = AsyncMock()

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        new_plaintext_key = "test_mock_api_key_new_12345"
        mock_service.generate_key = AsyncMock(
            return_value=(new_plaintext_key, mock_new_key)
        )

        # Call get_webhook_url with regenerate=True
        webhook_url = await whatsapp_action.get_webhook_url(regenerate=True)

        # Verify old key was revoked
        assert mock_old_key.is_active is False
        mock_context.save.assert_any_call(mock_old_key)

        # Verify new key was generated
        mock_service.generate_key.assert_called_once()

        # Verify new URL was set
        assert new_plaintext_key in webhook_url
        assert whatsapp_action.webhook_api_key_id == "o.APIKey.new123"
        assert whatsapp_action.webhook_url == webhook_url


@pytest.mark.asyncio
async def test_get_webhook_url_regenerates_on_ip_change(whatsapp_action, mock_agent):
    """Test that get_webhook_url regenerates when IP restriction changes."""
    # Set up existing webhook URL with IP restriction
    whatsapp_action.webhook_url = "http://localhost:8000/api/whatsapp/interact/webhook/n.Agent.test123?api_key=test_mock_old"
    whatsapp_action.webhook_api_key_id = "o.APIKey.old123"

    with patch(
        "jvagent.action.whatsapp.whatsapp_action.get_or_create_system_user"
    ) as mock_user, patch(
        "jvagent.action.whatsapp.whatsapp_action.APIKeyService"
    ) as mock_service_class, patch(
        "jvagent.action.whatsapp.whatsapp_action.get_prime_database"
    ) as mock_db, patch(
        "jvagent.action.whatsapp.whatsapp_action.GraphContext"
    ) as mock_context_class:

        # Setup mocks
        mock_user.return_value = "o.User.system123"
        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance

        mock_context = MagicMock(spec=GraphContext)
        mock_context_class.return_value = mock_context

        # Mock existing API key with different IP
        mock_existing_key = MagicMock(spec=APIKey)
        mock_existing_key.id = "o.APIKey.old123"
        mock_existing_key.is_active = True
        mock_existing_key.allowed_ips = ["192.0.2.0"]  # Different IP

        # Mock new API key generation
        mock_new_key = MagicMock(spec=APIKey)
        mock_new_key.id = "o.APIKey.new123"
        mock_new_key.key_prefix = "test_mock_new_"

        mock_context.get = AsyncMock(return_value=mock_existing_key)
        mock_context.save = AsyncMock()

        mock_service = MagicMock()
        mock_service_class.return_value = mock_service

        new_plaintext_key = "test_mock_api_key_new_12345"
        mock_service.generate_key = AsyncMock(
            return_value=(new_plaintext_key, mock_new_key)
        )

        # Call get_webhook_url with different IP
        webhook_url = await whatsapp_action.get_webhook_url(allowed_ip="203.0.113.0")

        # Verify new key was generated (IP didn't match)
        mock_service.generate_key.assert_called_once()
        call_kwargs = mock_service.generate_key.call_args[1]
        assert call_kwargs["allowed_ips"] == ["203.0.113.0"]

        # Verify new URL was set
        assert new_plaintext_key in webhook_url
        assert whatsapp_action.webhook_api_key_id == "o.APIKey.new123"
