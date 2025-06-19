"""
Security tests for URL validation bypass vulnerabilities.

This module tests that controller actions properly enforce domain restrictions
and cannot be used to bypass the allowed_domains security control.

@file purpose: Tests critical security fixes for URL validation bypass vulnerabilities
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from browser_use.browser.session import BrowserSession, BrowserError
from browser_use.browser.views import URLNotAllowedError
from browser_use.controller.service import Controller
from browser_use.controller.views import GoToUrlAction, SearchGoogleAction


class TestSecurityURLValidation:
    """Test security fixes for URL validation bypass vulnerabilities."""

    @pytest.fixture
    def mock_browser_session(self):
        """Create a mock BrowserSession with restricted domains."""
        session = MagicMock(spec=BrowserSession)
        session.navigate_to = AsyncMock()
        session.create_new_tab = AsyncMock()
        session.get_current_page = AsyncMock()
        
        # Mock a page object
        mock_page = MagicMock()
        mock_page.url = 'https://allowed.com'
        session.get_current_page.return_value = mock_page
        
        return session

    @pytest.fixture
    def controller(self):
        """Create a Controller instance for testing."""
        return Controller()

    @pytest.mark.asyncio
    async def test_go_to_url_respects_domain_restrictions(self, controller, mock_browser_session):
        """Test that go_to_url action properly validates URLs against allowed domains."""
        # Setup: Configure navigate_to to raise URLNotAllowedError for unauthorized domains
        mock_browser_session.navigate_to.side_effect = URLNotAllowedError("Navigation to non-allowed URL: https://malicious.com")
        
        # Test: Attempt to navigate to unauthorized domain
        action = GoToUrlAction(url="https://malicious.com")
        
        # Verify: Should raise URLNotAllowedError
        with pytest.raises(URLNotAllowedError, match="Navigation to non-allowed URL"):
            await controller.registry.execute_action(
                "go_to_url",
                {"url": "https://malicious.com"},
                browser_session=mock_browser_session
            )
        
        # Verify: navigate_to was called (not direct page.goto)
        mock_browser_session.navigate_to.assert_called_once_with("https://malicious.com")

    @pytest.mark.asyncio
    async def test_go_to_url_allows_authorized_domains(self, controller, mock_browser_session):
        """Test that go_to_url action allows navigation to authorized domains."""
        # Setup: Configure navigate_to to succeed for authorized domains
        mock_browser_session.navigate_to.return_value = None
        
        # Test: Navigate to authorized domain
        result = await controller.registry.execute_action(
            "go_to_url",
            {"url": "https://allowed.com"},
            browser_session=mock_browser_session
        )
        
        # Verify: Navigation succeeded
        assert result.success is True
        assert "Navigated to https://allowed.com" in result.extracted_content
        mock_browser_session.navigate_to.assert_called_once_with("https://allowed.com")

    @pytest.mark.asyncio
    async def test_search_google_respects_domain_restrictions(self, controller, mock_browser_session):
        """Test that search_google action properly validates URLs against allowed domains."""
        # Setup: Configure navigate_to to raise URLNotAllowedError for Google searches when Google is not allowed
        search_url = "https://www.google.com/search?q=test&udm=14"
        mock_browser_session.navigate_to.side_effect = URLNotAllowedError(f"Navigation to non-allowed URL: {search_url}")
        
        # Mock current page URL to trigger the navigate_to path
        mock_page = MagicMock()
        mock_page.url = "https://www.google.com"
        mock_browser_session.get_current_page.return_value = mock_page
        
        # Test: Attempt Google search when Google domain is not allowed
        with pytest.raises(URLNotAllowedError, match="Navigation to non-allowed URL"):
            await controller.registry.execute_action(
                "search_google",
                {"query": "test"},
                browser_session=mock_browser_session
            )
        
        # Verify: navigate_to was called (not direct page.goto)
        mock_browser_session.navigate_to.assert_called_once_with(search_url)

    @pytest.mark.asyncio
    async def test_search_google_uses_create_new_tab_when_not_on_google(self, controller, mock_browser_session):
        """Test that search_google uses create_new_tab when not already on Google."""
        # Setup: Configure create_new_tab to raise URLNotAllowedError for unauthorized domains
        search_url = "https://www.google.com/search?q=test&udm=14"
        mock_browser_session.create_new_tab.side_effect = URLNotAllowedError(f"Cannot create new tab with non-allowed URL: {search_url}")
        
        # Mock current page URL to NOT be Google
        mock_page = MagicMock()
        mock_page.url = "https://example.com"
        mock_browser_session.get_current_page.return_value = mock_page
        
        # Test: Attempt Google search when not on Google and Google domain is not allowed
        with pytest.raises(URLNotAllowedError, match="Cannot create new tab with non-allowed URL"):
            await controller.registry.execute_action(
                "search_google",
                {"query": "test"},
                browser_session=mock_browser_session
            )
        
        # Verify: create_new_tab was called (which includes its own URL validation)
        mock_browser_session.create_new_tab.assert_called_once_with(search_url)

    @pytest.mark.asyncio
    async def test_search_google_succeeds_when_google_allowed(self, controller, mock_browser_session):
        """Test that search_google succeeds when Google domain is allowed."""
        # Setup: Configure navigate_to to succeed
        mock_browser_session.navigate_to.return_value = None
        
        # Mock current page URL to be Google
        mock_page = MagicMock()
        mock_page.url = "https://www.google.com"
        mock_browser_session.get_current_page.return_value = mock_page
        
        # Test: Google search when Google domain is allowed
        result = await controller.registry.execute_action(
            "search_google",
            {"query": "test"},
            browser_session=mock_browser_session
        )
        
        # Verify: Search succeeded
        assert result.success is True
        assert 'Searched for "test" in Google' in result.extracted_content
        mock_browser_session.navigate_to.assert_called_once_with("https://www.google.com/search?q=test&udm=14")

    @pytest.mark.asyncio
    async def test_network_errors_handled_gracefully(self, controller, mock_browser_session):
        """Test that network errors in go_to_url are handled gracefully."""
        # Setup: Configure navigate_to to raise a network error
        mock_browser_session.navigate_to.side_effect = Exception("ERR_NAME_NOT_RESOLVED")
        
        # Test: Network error during navigation
        result = await controller.registry.execute_action(
            "go_to_url",
            {"url": "https://nonexistent.domain"},
            browser_session=mock_browser_session
        )
        
        # Verify: Network error handled gracefully
        assert result.success is False
        assert "Site unavailable" in result.error
        assert "ERR_NAME_NOT_RESOLVED" in result.error

    def test_security_fix_documentation(self):
        """Verify that security fixes are properly documented in the code."""
        # Read the controller service file to verify security fix comments are present
        with open("browser_use/controller/service.py", "r") as f:
            content = f.read()
        
        # Verify security fix comments are present
        assert "SECURITY FIX" in content, "Security fix should be documented in the code"
        assert "browser_session.navigate_to()" in content, "Should use secure navigation method"
        assert "URL validation against allowed_domains" in content, "Should mention URL validation purpose"


# NOTE: Not fully tested with Docker/browser automation due to automated environment limits
# Manual testing recommended for: Full browser integration, actual domain restriction enforcement
# These tests verify the security fix logic but not the complete browser session integration 