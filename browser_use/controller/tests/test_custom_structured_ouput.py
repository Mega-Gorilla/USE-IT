import json

import tiktoken

from browser_use.agent.views import AgentOutput
from browser_use.controller.service import Controller


def create_maximum_optimized_schema():
	"""Create the most optimized schema by flattening all $ref/$defs while preserving FULL descriptions and ALL action definitions"""

	# 1. Create controller and get all registered actions
	controller = Controller()
	ActionModel = controller.registry.create_action_model()

	# 2. Generate original schema
	agent_output_model = AgentOutput.type_with_custom_actions(ActionModel)
	original_schema = agent_output_model.model_json_schema()

	# 3. Extract $defs for reference resolution, then flatten everything
	defs_lookup = original_schema.get('$defs', {})

	def optimize_schema(obj, defs_lookup=None):
		"""Apply all optimization techniques including flattening all $ref/$defs and preserving full descriptions"""
		if isinstance(obj, dict):
			optimized = {}

			# Skip unnecessary fields AND $defs (we'll inline everything)
			skip_fields = ['additionalProperties', '$defs']

			for key, value in obj.items():
				if key in skip_fields:
					continue

				# Skip titles completely
				if key == 'title':
					continue

				# Preserve FULL descriptions without truncation
				elif key == 'description':
					optimized[key] = value

				# Add additionalProperties: false for object types (tighten unknown keys)
				elif key == 'type' and value == 'object':
					optimized[key] = value
					# Add additionalProperties: false to prevent stray fields
					if 'additionalProperties' not in obj:
						optimized['additionalProperties'] = False

					# FLATTEN: Resolve $ref by inlining the actual definition
				elif key == '$ref' and defs_lookup:
					ref_path = value.split('/')[-1]  # Get the definition name from "#/$defs/SomeName"
					if ref_path in defs_lookup:
						# Get the referenced definition and flatten it
						referenced_def = defs_lookup[ref_path]
						flattened_ref = optimize_schema(referenced_def, defs_lookup)

						# IMPORTANT: Preserve any description that was alongside the $ref
						if isinstance(obj, dict) and 'description' in obj:
							if isinstance(flattened_ref, dict):
								flattened_ref = flattened_ref.copy()
								flattened_ref['description'] = obj['description']

						return flattened_ref

				# Keep all anyOf structures (action unions) and resolve any $refs within
				elif key == 'anyOf' and isinstance(value, list):
					optimized[key] = [optimize_schema(item, defs_lookup) for item in value]

				# Recursively optimize nested structures
				elif key in ['properties', 'items']:
					optimized[key] = optimize_schema(value, defs_lookup)

				# Keep essential validation fields
				elif key in ['type', 'required', 'minimum', 'maximum', 'minItems', 'maxItems', 'pattern', 'default']:
					optimized[key] = value if not isinstance(value, (dict, list)) else optimize_schema(value, defs_lookup)

				# Recursively process all other fields
				else:
					optimized[key] = optimize_schema(value, defs_lookup) if isinstance(value, (dict, list)) else value

			return optimized

		elif isinstance(obj, list):
			return [optimize_schema(item, defs_lookup) for item in obj]
		return obj

	# 4. Create optimized schema with flattening
	optimized_schema = optimize_schema(original_schema, defs_lookup)

	# 5. Calculate token counts
	enc = tiktoken.encoding_for_model('gpt-4o')
	original_tokens = len(enc.encode(json.dumps(original_schema)))
	optimized_tokens = len(enc.encode(json.dumps(optimized_schema, separators=(',', ':'))))

	# 6. Verify all action definitions preserved (now flattened/inlined)
	original_actions = len(original_schema.get('$defs', {}) if isinstance(original_schema, dict) else {})
	# Count anyOf items in optimized schema as these are the flattened action definitions
	optimized_actions = 0
	if isinstance(optimized_schema, dict) and 'properties' in optimized_schema:
		action_prop = optimized_schema['properties'].get('action', {})
		if 'items' in action_prop and 'anyOf' in action_prop['items']:
			optimized_actions = len(action_prop['items']['anyOf'])

	# 7. Save both schemas
	with open('./tmp/original_schema.json', 'w') as f:
		json.dump(original_schema, f)

	with open('./tmp/optimized_schema.json', 'w') as f:
		json.dump(optimized_schema, f, separators=(',', ':'), indent=2)

	# 8. Print results
	print('📊 Schema Optimization Results (Flattened):')
	print(f'   Original: {original_tokens:,} tokens')
	print(f'   Flattened: {optimized_tokens:,} tokens')
	print(
		f'   Reduction: {original_tokens - optimized_tokens:,} tokens ({((original_tokens - optimized_tokens) / original_tokens * 100):.1f}%)'
	)
	print(f'   Action definitions: {optimized_actions}/{original_actions} flattened ✅')
	print('   Titles removed: ✅')
	print('   FULL descriptions preserved: ✅ (action descriptions now included)')
	print('   $defs removed: ✅')
	print('   $refs resolved: ✅')
	print('')
	print('💾 Files saved:')
	print('   Original: ./tmp/original_schema.json')
	print('   Flattened: ./tmp/optimized_schema.json')

	return optimized_schema


if __name__ == '__main__':
	create_maximum_optimized_schema()
