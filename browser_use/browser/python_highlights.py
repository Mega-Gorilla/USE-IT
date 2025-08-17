"""Python-based highlighting system for drawing bounding boxes on screenshots.

This module replaces JavaScript-based highlighting with fast Python image processing
to draw bounding boxes around interactive elements directly on screenshots.
"""

import base64
import io
from typing import Dict, Any, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
import logging

from browser_use.dom.serializer.types import DOMSelectorMap

logger = logging.getLogger(__name__)

# Color scheme for different element types
ELEMENT_COLORS = {
    'button': '#FF6B6B',      # Red for buttons
    'input': '#4ECDC4',       # Teal for inputs
    'select': '#45B7D1',      # Blue for dropdowns
    'a': '#96CEB4',           # Green for links
    'textarea': '#FFEAA7',    # Yellow for text areas
    'default': '#DDA0DD',     # Light purple for other interactive elements
}

# Element type mappings
ELEMENT_TYPE_MAP = {
    'button': 'button',
    'input': 'input',
    'select': 'select',
    'a': 'a',
    'textarea': 'textarea',
}

def get_element_color(tag_name: str, element_type: Optional[str] = None) -> str:
    """Get color for element based on tag name and type."""
    # Check input type first
    if tag_name == 'input' and element_type:
        if element_type in ['button', 'submit']:
            return ELEMENT_COLORS['button']
    
    # Use tag-based color
    return ELEMENT_COLORS.get(tag_name.lower(), ELEMENT_COLORS['default'])

def should_show_text_overlay(text: Optional[str]) -> bool:
    """Determine if text overlay should be shown based on length."""
    if not text:
        return False
    return len(text.strip()) <= 10

def draw_bounding_box_with_text(
    draw: ImageDraw.Draw, 
    bbox: Tuple[int, int, int, int], 
    color: str, 
    text: Optional[str] = None,
    font: Optional[ImageFont.FreeTypeFont] = None
) -> None:
    """Draw a bounding box with optional text overlay."""
    x1, y1, x2, y2 = bbox
    
    # Draw bounding box with 2px width
    for i in range(2):
        draw.rectangle([x1 + i, y1 + i, x2 - i, y2 - i], outline=color, fill=None)
    
    # Draw text overlay if provided and short enough
    if text and should_show_text_overlay(text):
        try:
            # Get text size
            if font:
                bbox_text = draw.textbbox((0, 0), text, font=font)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
            else:
                # Fallback for default font
                bbox_text = draw.textbbox((0, 0), text)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
            
            # Position text at top-left of bounding box
            text_x = max(0, x1)
            text_y = max(0, y1 - text_height - 2)  # Above the box
            
            # Draw background rectangle for text
            draw.rectangle(
                [text_x - 2, text_y - 2, text_x + text_width + 2, text_y + text_height + 2],
                fill=color,
                outline=None
            )
            
            # Draw text
            draw.text((text_x, text_y), text, fill='white', font=font)
            
        except Exception as e:
            logger.debug(f"Failed to draw text overlay: {e}")

def create_highlighted_screenshot(
    screenshot_b64: str,
    selector_map: DOMSelectorMap,
    device_pixel_ratio: float = 1.0,
    viewport_offset_x: int = 0,
    viewport_offset_y: int = 0
) -> str:
    """Create a highlighted screenshot with bounding boxes around interactive elements.
    
    Args:
        screenshot_b64: Base64 encoded screenshot
        selector_map: Map of interactive elements with their positions
        device_pixel_ratio: Device pixel ratio for scaling coordinates
        viewport_offset_x: X offset for viewport positioning
        viewport_offset_y: Y offset for viewport positioning
        
    Returns:
        Base64 encoded highlighted screenshot
    """
    try:
        # Decode screenshot
        screenshot_data = base64.b64decode(screenshot_b64)
        image = Image.open(io.BytesIO(screenshot_data)).convert('RGBA')
        
        # Create drawing context
        draw = ImageDraw.Draw(image)
        
        # Try to load a font, fall back to default if not available
        font = None
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
        except:
            try:
                font = ImageFont.truetype("arial.ttf", 12)
            except:
                font = None  # Use default font
        
        # Process each interactive element
        for element_id, element in selector_map.items():
            try:
                # Get element bounds
                bounds = element.bounds
                if not bounds:
                    continue
                
                # Scale coordinates by device pixel ratio and apply viewport offset
                x1 = int((bounds.x + viewport_offset_x) * device_pixel_ratio)
                y1 = int((bounds.y + viewport_offset_y) * device_pixel_ratio)
                x2 = int((bounds.x + bounds.width + viewport_offset_x) * device_pixel_ratio)
                y2 = int((bounds.y + bounds.height + viewport_offset_y) * device_pixel_ratio)
                
                # Ensure coordinates are within image bounds
                img_width, img_height = image.size
                x1 = max(0, min(x1, img_width))
                y1 = max(0, min(y1, img_height))
                x2 = max(x1, min(x2, img_width))
                y2 = max(y1, min(y2, img_height))
                
                # Skip if bounding box is too small or invalid
                if x2 - x1 < 2 or y2 - y1 < 2:
                    continue
                
                # Get element color based on type
                tag_name = element.tag_name if hasattr(element, 'tag_name') else 'div'
                element_type = None
                if hasattr(element, 'attributes') and element.attributes:
                    element_type = element.attributes.get('type')
                
                color = get_element_color(tag_name, element_type)
                
                # Get text for overlay (if short enough)
                text = None
                if hasattr(element, 'text') and element.text:
                    text = element.text.strip()
                elif hasattr(element, 'attributes') and element.attributes:
                    # Try to get meaningful text from attributes
                    text = (element.attributes.get('aria-label') or 
                           element.attributes.get('title') or 
                           element.attributes.get('placeholder') or 
                           element.attributes.get('value', ''))
                
                # Draw bounding box with optional text
                draw_bounding_box_with_text(
                    draw, (x1, y1, x2, y2), color, text, font
                )
                
            except Exception as e:
                logger.debug(f"Failed to draw highlight for element {element_id}: {e}")
                continue
        
        # Convert back to base64
        output_buffer = io.BytesIO()
        image.save(output_buffer, format='PNG')
        output_buffer.seek(0)
        
        highlighted_b64 = base64.b64encode(output_buffer.getvalue()).decode('utf-8')
        
        logger.debug(f"Successfully created highlighted screenshot with {len(selector_map)} elements")
        return highlighted_b64
        
    except Exception as e:
        logger.error(f"Failed to create highlighted screenshot: {e}")
        # Return original screenshot on error
        return screenshot_b64

def get_viewport_info_from_cdp(cdp_session) -> Tuple[float, int, int]:
    """Get viewport information from CDP session.
    
    Returns:
        Tuple of (device_pixel_ratio, viewport_offset_x, viewport_offset_y)
    """
    # This is a placeholder - in real implementation, you'd get this from CDP
    # For now, return sensible defaults
    return 1.0, 0, 0

async def create_highlighted_screenshot_async(
    screenshot_b64: str,
    selector_map: DOMSelectorMap,
    cdp_session = None
) -> str:
    """Async wrapper for creating highlighted screenshots.
    
    Args:
        screenshot_b64: Base64 encoded screenshot
        selector_map: Map of interactive elements
        cdp_session: CDP session for getting viewport info
        
    Returns:
        Base64 encoded highlighted screenshot
    """
    # Get viewport information if CDP session is available
    device_pixel_ratio = 1.0
    viewport_offset_x = 0
    viewport_offset_y = 0
    
    if cdp_session:
        try:
            device_pixel_ratio, viewport_offset_x, viewport_offset_y = get_viewport_info_from_cdp(cdp_session)
        except Exception as e:
            logger.debug(f"Failed to get viewport info from CDP: {e}")
    
    # Create highlighted screenshot (run in thread pool if needed for performance)
    return create_highlighted_screenshot(
        screenshot_b64,
        selector_map,
        device_pixel_ratio,
        viewport_offset_x,
        viewport_offset_y
    )