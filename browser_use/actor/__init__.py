"""CDP-Use High-Level Library

A Playwright-like library built on top of CDP (Chrome DevTools Protocol).
"""

from .browser import Browser
from .element import Element
from .mouse import Mouse
from .target import Target

__all__ = ['Browser', 'Target', 'Element', 'Mouse']
