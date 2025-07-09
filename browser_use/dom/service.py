import asyncio
import time

from cdp_use import CDPClient
from cdp_use.cdp.accessibility.commands import GetFullAXTreeReturns
from cdp_use.cdp.accessibility.types import AXNode, AXPropertyName
from cdp_use.cdp.dom.commands import GetDocumentReturns
from cdp_use.cdp.dom.types import Node
from cdp_use.cdp.domsnapshot.commands import CaptureSnapshotReturns

from browser_use.browser import Browser
from browser_use.dom.views import EnhancedAXNode, EnhancedAXProperty, EnhancedDOMTreeNode, NodeType


class DOMService:
	def __init__(self, browser: Browser):
		self.browser = browser

		self.cdp_client: CDPClient | None = None
		self.playwright_page_to_session_id_store: dict[str, str] = {}

	async def _get_cdp_client(self) -> CDPClient:
		if self.cdp_client is None:
			if not self.browser.cdp_url:
				raise ValueError('CDP URL is not set')

			self.cdp_client = CDPClient(self.browser.cdp_url)
			await self.cdp_client.start()
		return self.cdp_client

	# on self destroy -> stop the cdp client
	async def __del__(self):
		if self.cdp_client:
			await self.cdp_client.stop()
			self.cdp_client = None

	async def _get_current_page_session_id(self) -> str:
		"""Get the target ID for a playwright page.

		TODO: this is a REALLY hacky way -> if multiple same urls are open then this will break
		"""
		page = await self.browser.get_current_page()
		page_guid = page._impl_obj._guid

		# if page_guid in self.page_to_session_id_store:
		# 	return self.page_to_session_id_store[page_guid]

		cdp_client = await self._get_cdp_client()

		targets = await cdp_client.send.Target.getTargets()
		for target in targets['targetInfos']:
			if target['type'] == 'page' and target['url'] == page.url:
				# cache the session id for this playwright page
				self.playwright_page_to_session_id_store[page_guid] = target['targetId']

				session = await cdp_client.send.Target.attachToTarget(params={'targetId': target['targetId'], 'flatten': True})
				session_id = session['sessionId']

				await cdp_client.send.Target.setAutoAttach(
					params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}
				)

				await cdp_client.send.DOM.enable(session_id=session_id)
				await cdp_client.send.Accessibility.enable(session_id=session_id)

				return session_id

		raise ValueError(f'No session id found for page {page.url}')

	def _build_enhanced_ax_node(self, ax_node: AXNode) -> EnhancedAXNode:
		properties: list[EnhancedAXProperty] | None = None
		if 'properties' in ax_node and ax_node['properties']:
			properties = []
			for property in ax_node['properties']:
				try:
					# test whether property name can go into the enum (sometimes Chrome returns some random properties)
					AXPropertyName(property['name'])
					properties.append(
						EnhancedAXProperty(
							name=property['name'],
							value=property.get('value', {}).get('value', None),
							# related_nodes=[],  # TODO: add related nodes
						)
					)
				except ValueError:
					pass

		enhanced_ax_node = EnhancedAXNode(
			ax_node_id=ax_node['nodeId'],
			ignored=ax_node['ignored'],
			role=ax_node.get('role', {}).get('value', None),
			name=ax_node.get('name', {}).get('value', None),
			description=ax_node.get('description', {}).get('value', None),
			properties=properties,
		)
		return enhanced_ax_node

	def _build_enhanced_dom_tree(
		self, dom_tree: GetDocumentReturns, ax_tree: GetFullAXTreeReturns, snapshot: CaptureSnapshotReturns
	) -> EnhancedDOMTreeNode:
		ax_tree_lookup: dict[int, AXNode] = {
			ax_node['backendDOMNodeId']: ax_node for ax_node in ax_tree['nodes'] if 'backendDOMNodeId' in ax_node
		}

		enhanced_dom_tree_node_lookup: dict[int, EnhancedDOMTreeNode] = {}
		""" NodeId (NOT backend node id) -> enhanced dom tree node"""  # way to get the parent/content node

		def _construct_enhanced_node(node: Node) -> EnhancedDOMTreeNode:
			# memoize the mf (I don't know if some nodes are duplicated)
			if node['nodeId'] in enhanced_dom_tree_node_lookup:
				return enhanced_dom_tree_node_lookup[node['nodeId']]

			ax_node = ax_tree_lookup.get(node['backendNodeId'])
			if ax_node:
				enhanced_ax_node = self._build_enhanced_ax_node(ax_node)
			else:
				enhanced_ax_node = None

			# To make attributes more readable
			attributes: dict[str, str] | None = None
			if 'attributes' in node and node['attributes']:
				attributes = {}
				for i in range(0, len(node['attributes']), 2):
					attributes[node['attributes'][i]] = node['attributes'][i + 1]

			dom_tree_node = EnhancedDOMTreeNode(
				node_id=node['nodeId'],
				backend_node_id=node['backendNodeId'],
				node_type=NodeType(node['nodeType']),
				node_name=node['nodeName'],
				node_value=node['nodeValue'],
				attributes=attributes,
				is_scrollable=node.get('isScrollable', None),
				frame_id=node.get('frameId', None),
				content_document=None,
				shadow_root_type=node.get('shadowRootType', None),
				shadow_roots=None,
				parent_node=None,
				children_nodes=None,
				ax_node=enhanced_ax_node,
			)

			enhanced_dom_tree_node_lookup[node['nodeId']] = dom_tree_node

			if 'parentId' in node and node['parentId']:
				dom_tree_node.parent_node = enhanced_dom_tree_node_lookup[
					node['parentId']
				]  # parents should always be in the lookup

			if 'contentDocument' in node and node['contentDocument']:
				dom_tree_node.content_document = _construct_enhanced_node(node['contentDocument'])  # maybe new maybe not, idk

			if 'shadowRoots' in node and node['shadowRoots']:
				dom_tree_node.shadow_roots = []
				for shadow_root in node['shadowRoots']:
					dom_tree_node.shadow_roots.append(_construct_enhanced_node(shadow_root))

			if 'children' in node and node['children']:
				dom_tree_node.children_nodes = []
				for child in node['children']:
					dom_tree_node.children_nodes.append(_construct_enhanced_node(child))

			return dom_tree_node

		enhanced_dom_tree_node = _construct_enhanced_node(dom_tree['root'])

		return enhanced_dom_tree_node

	async def get_dom_tree(self) -> EnhancedDOMTreeNode:
		if not self.browser.cdp_url:
			raise ValueError('CDP URL is not set')

		session_id = await self._get_current_page_session_id()
		cdp_client = await self._get_cdp_client()

		snapshot_request = cdp_client.send.DOMSnapshot.captureSnapshot(params={'computedStyles': []}, session_id=session_id)

		dom_tree_request = cdp_client.send.DOM.getDocument(params={'depth': -1, 'pierce': True}, session_id=session_id)

		ax_tree_request = cdp_client.send.Accessibility.getFullAXTree(session_id=session_id)

		start = time.time()
		snapshot, dom_tree, ax_tree = await asyncio.gather(snapshot_request, dom_tree_request, ax_tree_request)
		end = time.time()
		print(f'Time taken to get dom tree: {end - start} seconds')

		start = time.time()
		enhanced_dom_tree = self._build_enhanced_dom_tree(dom_tree, ax_tree, snapshot)
		end = time.time()
		print(f'Time taken to build enhanced dom tree: {end - start} seconds')

		return enhanced_dom_tree
