"""MCP (Model Context Protocol) support for browser-use.

This module provides integration with MCP servers and clients for browser automation.
"""

from typing import TYPE_CHECKING

# Type stubs for lazy imports
if TYPE_CHECKING:
	from browser_use.mcp.client import MCPClient
	from browser_use.mcp.controller import MCPToolWrapper
	from browser_use.mcp.server import BrowserUseServer

# Lazy imports mapping
_LAZY_IMPORTS = {
	'MCPClient': ('browser_use.mcp.client', 'MCPClient'),
	'MCPToolWrapper': ('browser_use.mcp.controller', 'MCPToolWrapper'),
	'BrowserUseServer': ('browser_use.mcp.server', 'BrowserUseServer'),
}


def __getattr__(name: str):
	"""Lazy import to avoid importing heavy modules when not needed."""
	if name in _LAZY_IMPORTS:
		module_path, attr_name = _LAZY_IMPORTS[name]
		try:
			from importlib import import_module

			module = import_module(module_path)
			attr = getattr(module, attr_name)
			# Cache the imported attribute in the module's globals
			globals()[name] = attr
			return attr
		except ImportError as e:
			raise ImportError(f'Failed to import {name} from {module_path}: {e}') from e

	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = ['MCPClient', 'MCPToolWrapper', 'BrowserUseServer']
