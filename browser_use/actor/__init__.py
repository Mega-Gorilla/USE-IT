"""CDP-Use High-Level Library

A Playwright-like library built on top of CDP (Chrome DevTools Protocol).
"""

from .element import Element
from .mouse import Mouse
from .target import Target

__all__ = ['Target', 'Element', 'Mouse']
