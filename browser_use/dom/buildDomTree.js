(doHighlightElements = true) => {
    let highlightIndex = 0;
    const COLORS = [
        '#FF4444', // Red
        '#44AA44', // Darker Green
        '#4444FF', // Blue
        '#FF44FF', // Magenta
        '#44CCCC', // Darker Cyan
        '#FF8844', // Orange
        '#9944FF', // Purple
        '#FF4488', // Pink
    ];

    const usedPositions = new Set();

    // Move createHighlightContainer to the top level scope
    function createHighlightContainer() {
        let container = document.getElementById('playwright-highlight-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'playwright-highlight-container';
            container.style.position = 'fixed';
            container.style.pointerEvents = 'none';
            container.style.top = '0';
            container.style.left = '0';
            container.style.width = '100%';
            container.style.height = '100%';
            container.style.zIndex = '2147483647';
            document.documentElement.appendChild(container);
        }
        return container;
    }

    function getIframeOffset(iframe) {
        let offset = { top: 0, left: 0 };
        try {
            if (!iframe) return offset;
            
            const rect = iframe.getBoundingClientRect();
            offset.top = rect.top;
            offset.left = rect.left;

            if (iframe.contentWindow) {
                offset.top += iframe.contentWindow.scrollY || 0;
                offset.left += iframe.contentWindow.scrollX || 0;
            }

            offset.top += window.scrollY;
            offset.left += window.scrollX;

            return offset;
        } catch (e) {
            console.warn('Error calculating iframe offset:', e);
            return offset;
        }
    }

    function getAbsolutePosition(element, parentIframe) {
        const rect = element.getBoundingClientRect();
        const iframeOffset = getIframeOffset(parentIframe);
        
        return {
            top: rect.top + iframeOffset.top,
            left: rect.left + iframeOffset.left,
            width: rect.width,
            height: rect.height
        };
    }

    function findOptimalLabelPosition(elementPos, viewport) {
        const isSmallElement = elementPos.width < 30 || elementPos.height < 20;
        const labelDims = {
            width: isSmallElement ? 16 : 20,
            height: isSmallElement ? 14 : 16
        };
        const margin = 4;
    
        // Helper to check if positions are horizontally adjacent
        function isHorizontallyAdjacent(pos) {
            return Array.from(usedPositions).some(usedPos => {
                const [usedTop, usedLeft] = usedPos.split(',').map(Number);
                const verticalOverlap = Math.abs(usedTop - pos.top) < labelDims.height;
                const horizontalProximity = Math.abs(usedLeft - pos.left) < labelDims.width * 2;
                return verticalOverlap && horizontalProximity;
            });
        }
    
        // Calculate positions relative to the element
        const positions = [
            // Left side (default)
            {
                top: elementPos.top + (elementPos.height - labelDims.height) / 2,
                left: elementPos.left - labelDims.width - margin,
                score: 100,
                position: 'left'
            },
            // Inside (if large enough)
            elementPos.width >= 40 && elementPos.height >= 25 ? {
                top: elementPos.top + margin,
                left: elementPos.left + margin,
                score: 90,
                position: 'inside'
            } : null,
            // Above
            {
                top: elementPos.top - labelDims.height - margin,
                left: elementPos.left + (elementPos.width - labelDims.width) / 2,
                score: 80,
                position: 'above'
            },
            // Below
            {
                top: elementPos.top + elementPos.height + margin,
                left: elementPos.left + (elementPos.width - labelDims.width) / 2,
                score: 70,
                position: 'below'
            },
            // Right
            {
                top: elementPos.top + (elementPos.height - labelDims.height) / 2,
                left: elementPos.left + elementPos.width + margin,
                score: 60,
                position: 'right'
            }
        ].filter(Boolean);
    
        return positions
            .map(pos => {
                let score = pos.score;
                const posKey = `${Math.round(pos.top)},${Math.round(pos.left)}`;
    
                // Check for existing labels
                if (usedPositions.has(posKey)) {
                    score -= 100; // Make it very unlikely to overlap
                }
    
                // Heavily penalize horizontally adjacent positions
                if (isHorizontallyAdjacent(pos)) {
                    score -= 50;
                }
    
                // Check viewport bounds
                if (pos.top < 0 || pos.top + labelDims.height > viewport.height ||
                    pos.left < 0 || pos.left + labelDims.width > viewport.width) {
                    score -= 60;
                }
    
                // Check for element overlaps
                try {
                    const elementsAtPos = document.elementsFromPoint(pos.left, pos.top);
                    if (elementsAtPos.length > 2) {
                        score -= 40;
                    }
                } catch (e) {
                    score -= 20;
                }
    
                // Prefer above/below for horizontally adjacent elements
                if (pos.position === 'above' || pos.position === 'below') {
                    const hasHorizontalNeighbors = Array.from(usedPositions).some(usedPos => {
                        const [usedTop, usedLeft] = usedPos.split(',').map(Number);
                        return Math.abs(usedLeft - elementPos.left) < elementPos.width * 2;
                    });
                    if (hasHorizontalNeighbors) {
                        score += 30;
                    }
                }
    
                return { ...pos, score };
            })
            .sort((a, b) => b.score - a.score)[0];
    }

    function highlightElement(element, index, parentIframe = null) {
        const container = createHighlightContainer(); // Now this will work
        const color = COLORS[index % COLORS.length];
        const pos = getAbsolutePosition(element, parentIframe);
        const viewport = {
            width: window.innerWidth,
            height: window.innerHeight
        };

        // Create highlight box
        const box = document.createElement('div');
        Object.assign(box.style, {
            position: 'absolute',
            border: `2px solid ${color}`,
            backgroundColor: `${color}11`,
            boxSizing: 'border-box',
            pointerEvents: 'none',
            top: `${pos.top}px`,
            left: `${pos.left}px`,
            width: `${pos.width}px`,
            height: `${pos.height}px`
        });

        // Create label
        const label = document.createElement('div');
        const isSmallElement = pos.width < 30 || pos.height < 20;
        Object.assign(label.style, {
            position: 'absolute',
            background: color,
            color: 'white',
            padding: isSmallElement ? '1px 3px' : '2px 4px',
            borderRadius: '3px',
            fontSize: isSmallElement ? '9px' : '10px',
            lineHeight: '1',
            whiteSpace: 'nowrap',
            fontWeight: 'bold',
            zIndex: 2147483647
        });
        label.textContent = index;

        const labelPos = findOptimalLabelPosition(pos, viewport);
        Object.assign(label.style, {
            top: `${labelPos.top}px`,
            left: `${labelPos.left}px`
        });

        usedPositions.add(`${Math.round(labelPos.top)},${Math.round(labelPos.left)}`);

        container.appendChild(box);
        container.appendChild(label);

        element.setAttribute('browser-user-highlight-id', `playwright-highlight-${index}`);
    }

    // Helper function to generate XPath as a tree
    function getXPathTree(element, stopAtBoundary = true) {
        const segments = [];
        let currentElement = element;

        while (currentElement && currentElement.nodeType === Node.ELEMENT_NODE) {
            // Stop if we hit a shadow root or iframe
            if (stopAtBoundary && (currentElement.parentNode instanceof ShadowRoot || currentElement.parentNode instanceof HTMLIFrameElement)) {
                break;
            }

            let index = 0;
            let sibling = currentElement.previousSibling;
            while (sibling) {
                if (sibling.nodeType === Node.ELEMENT_NODE &&
                    sibling.nodeName === currentElement.nodeName) {
                    index++;
                }
                sibling = sibling.previousSibling;
            }

            const tagName = currentElement.nodeName.toLowerCase();
            const xpathIndex = index > 0 ? `[${index + 1}]` : '';
            segments.unshift(`${tagName}${xpathIndex}`);

            currentElement = currentElement.parentNode;
        }

        return segments.join('/');
    }

    // Helper function to check if element is accepted
    function isElementAccepted(element) {
        const leafElementDenyList = new Set(['svg', 'script', 'style', 'link', 'meta']);
        return !leafElementDenyList.has(element.tagName.toLowerCase());
    }

    // Helper function to check if element is interactive
    function isInteractiveElement(element) {
        // Base interactive elements and roles
        const interactiveElements = new Set([
            'a', 'button', 'details', 'embed', 'input', 'label',
            'menu', 'menuitem', 'object', 'select', 'textarea', 'summary', 'th-tds-button-link'
        ]);

        const interactiveRoles = new Set([
            'button', 'menu', 'menuitem', 'link', 'checkbox', 'radio',
            'slider', 'tab', 'tabpanel', 'textbox', 'combobox', 'grid',
            'listbox', 'option', 'progressbar', 'scrollbar', 'searchbox',
            'switch', 'tree', 'treeitem', 'spinbutton', 'tooltip',
            'menuitemcheckbox', 'menuitemradio'
        ]);

        const tagName = element.tagName.toLowerCase();
        const role = element.getAttribute('role');
        const ariaRole = element.getAttribute('aria-role');
        const tabIndex = element.getAttribute('tabindex');

        // Basic role/attribute checks
        const hasInteractiveRole = interactiveElements.has(tagName) ||
            interactiveRoles.has(role) ||
            interactiveRoles.has(ariaRole) ||
            tabIndex === '0';

        if (hasInteractiveRole) return true;

        // Get computed style
        const style = window.getComputedStyle(element);

        // Check if element has click-like styling
        // const hasClickStyling = style.cursor === 'pointer' ||
        //     element.style.cursor === 'pointer' ||
        //     style.pointerEvents !== 'none';

        // Check for event listeners
        const hasClickHandler = element.onclick !== null ||
            element.getAttribute('onclick') !== null ||
            element.hasAttribute('ng-click') ||
            element.hasAttribute('@click') ||
            element.hasAttribute('v-on:click');

        // Helper function to safely get event listeners
        function getEventListeners(el) {
            try {
                // Try to get listeners using Chrome DevTools API
                return window.getEventListeners?.(el) || {};
            } catch (e) {
                // Fallback: check for common event properties
                const listeners = {};

                // List of common event types to check
                const eventTypes = [
                    'click', 'mousedown', 'mouseup',
                    'touchstart', 'touchend',
                    'keydown', 'keyup', 'focus', 'blur'
                ];

                for (const type of eventTypes) {
                    const handler = el[`on${type}`];
                    if (handler) {
                        listeners[type] = [{
                            listener: handler,
                            useCapture: false
                        }];
                    }
                }

                return listeners;
            }
        }

        // Check for click-related events on the element itself
        const listeners = getEventListeners(element);
        const hasClickListeners = listeners && (
            listeners.click?.length > 0 ||
            listeners.mousedown?.length > 0 ||
            listeners.mouseup?.length > 0 ||
            listeners.touchstart?.length > 0 ||
            listeners.touchend?.length > 0
        );

        // Check for ARIA properties that suggest interactivity
        const hasAriaProps = element.hasAttribute('aria-expanded') ||
            element.hasAttribute('aria-pressed') ||
            element.hasAttribute('aria-selected') ||
            element.hasAttribute('aria-checked');

        // Check for form-related functionality
        const isFormRelated = element.form !== undefined ||
            element.hasAttribute('contenteditable') ||
            style.userSelect !== 'none';

        // Check if element is draggable
        const isDraggable = element.draggable ||
            element.getAttribute('draggable') === 'true';

        return hasAriaProps ||
            // hasClickStyling ||
            hasClickHandler ||
            hasClickListeners ||
            // isFormRelated ||
            isDraggable;

    }

    // Helper function to check if element is visible
    function isElementVisible(element) {
        const style = window.getComputedStyle(element);
        return element.offsetWidth > 0 &&
            element.offsetHeight > 0 &&
            style.visibility !== 'hidden' &&
            style.display !== 'none';
    }

    // Helper function to check if element is the top element at its position
    function isTopElement(element) {
        // Find the correct document context and root element
        let doc = element.ownerDocument;

        // If we're in an iframe, elements are considered top by default
        if (doc !== window.document) {
            return true;
        }

        // For shadow DOM, we need to check within its own root context
        const shadowRoot = element.getRootNode();
        if (shadowRoot instanceof ShadowRoot) {
            const rect = element.getBoundingClientRect();
            const point = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };

            try {
                // Use shadow root's elementFromPoint to check within shadow DOM context
                const topEl = shadowRoot.elementFromPoint(point.x, point.y);
                if (!topEl) return false;

                // Check if the element or any of its parents match our target element
                let current = topEl;
                while (current && current !== shadowRoot) {
                    if (current === element) return true;
                    current = current.parentElement;
                }
                return false;
            } catch (e) {
                return true; // If we can't determine, consider it visible
            }
        }

        // Regular DOM elements
        const rect = element.getBoundingClientRect();
        const point = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };

        try {
            const topEl = document.elementFromPoint(point.x, point.y);
            if (!topEl) return false;

            let current = topEl;
            while (current && current !== document.documentElement) {
                if (current === element) return true;
                current = current.parentElement;
            }
            return false;
        } catch (e) {
            return true;
        }
    }

    // Helper function to check if text node is visible
    function isTextNodeVisible(textNode) {
        const range = document.createRange();
        range.selectNodeContents(textNode);
        const rect = range.getBoundingClientRect();

        return rect.width !== 0 &&
            rect.height !== 0 &&
            rect.top >= 0 &&
            rect.top <= window.innerHeight &&
            textNode.parentElement?.checkVisibility({
                checkOpacity: true,
                checkVisibilityCSS: true
            });
    }


    // Function to traverse the DOM and create nested JSON
    function buildDomTree(node, parentIframe = null) {
        if (!node) return null;

        // Special case for text nodes
        if (node.nodeType === Node.TEXT_NODE) {
            const textContent = node.textContent.trim();
            if (textContent && isTextNodeVisible(node)) {
                return {
                    type: "TEXT_NODE",
                    text: textContent,
                    isVisible: true,
                };
            }
            return null;
        }

        // Check if element is accepted
        if (node.nodeType === Node.ELEMENT_NODE && !isElementAccepted(node)) {
            return null;
        }

        const nodeData = {
            tagName: node.tagName ? node.tagName.toLowerCase() : null,
            attributes: {},
            xpath: node.nodeType === Node.ELEMENT_NODE ? getXPathTree(node, true) : null,
            children: [],
        };

        // Copy all attributes if the node is an element
        if (node.nodeType === Node.ELEMENT_NODE && node.attributes) {
            // Use getAttributeNames() instead of directly iterating attributes
            const attributeNames = node.getAttributeNames?.() || [];
            for (const name of attributeNames) {
                nodeData.attributes[name] = node.getAttribute(name);
            }
        }

        if (node.nodeType === Node.ELEMENT_NODE) {
            const isInteractive = isInteractiveElement(node);
            const isVisible = isElementVisible(node);
            const isTop = isTopElement(node);

            nodeData.isInteractive = isInteractive;
            nodeData.isVisible = isVisible;
            nodeData.isTopElement = isTop;

            // Highlight if element meets all criteria and highlighting is enabled
            if (isInteractive && isVisible && isTop) {
                nodeData.highlightIndex = highlightIndex++;
                if (doHighlightElements) {
                    highlightElement(node, nodeData.highlightIndex, parentIframe);
                }
            }
        }

        // Only add iframeContext if we're inside an iframe
        // if (parentIframe) {
        //     nodeData.iframeContext = `iframe[src="${parentIframe.src || ''}"]`;
        // }

        // Only add shadowRoot field if it exists
        if (node.shadowRoot) {
            nodeData.shadowRoot = true;
        }

        // Handle shadow DOM
        if (node.shadowRoot) {
            const shadowChildren = Array.from(node.shadowRoot.childNodes).map(child =>
                buildDomTree(child, parentIframe)
            );
            nodeData.children.push(...shadowChildren);
        }

        // Handle iframes
        if (node.tagName === 'IFRAME') {
            try {
                const iframeDoc = node.contentDocument || node.contentWindow.document;
                if (iframeDoc) {
                    const iframeChildren = Array.from(iframeDoc.body.childNodes).map(child =>
                        buildDomTree(child, node)
                    );
                    nodeData.children.push(...iframeChildren);
                }
            } catch (e) {
                console.warn('Unable to access iframe:', node);
            }
        } else {
            const children = Array.from(node.childNodes).map(child =>
                buildDomTree(child, parentIframe)
            );
            nodeData.children.push(...children);
        }

        return nodeData;
    }


    return buildDomTree(document.body);
}