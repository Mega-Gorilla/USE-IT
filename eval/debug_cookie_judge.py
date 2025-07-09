#!/usr/bin/env python3
"""
Diagnostic script for debugging cookie judge issues.
Usage: python debug_cookie_judge.py <task_id>
"""

import asyncio
import json
import sys
from pathlib import Path

try:
	from cookie_judge import evaluate_task_with_login_cookie
except ImportError:
	# Fallback for when running from different directory
	import sys

	sys.path.insert(0, '.')
	from cookie_judge import evaluate_task_with_login_cookie


async def debug_cookie_judge(task_id: str, login_cookie: str | None = None):
	"""Debug cookie judge evaluation for a specific task"""

	task_folder = Path(f'saved_trajectories/{task_id}')

	if not task_folder.exists():
		print(f'❌ Task folder not found: {task_folder}')
		return

	print(f'🔍 Debugging cookie judge for task: {task_id}')
	print(f'📁 Task folder: {task_folder}')

	# Check what files exist
	print('\n📋 Files in task folder:')
	for file_path in task_folder.iterdir():
		print(f'  - {file_path.name}')

	# Check result.json
	result_file = task_folder / 'result.json'
	if result_file.exists():
		try:
			with open(result_file) as f:
				result_data = json.load(f)
			print('\n📄 Result.json data:')
			print(f'  - Task: {result_data.get("task", "N/A")[:100]}...')
			print(f'  - Steps: {result_data.get("steps", "N/A")}')
			print(f'  - Self-report success: {result_data.get("self_report_success", "N/A")}')

			# Check if this is a login task
			if login_cookie is None:
				print('  - ⚠️ No login_cookie provided for testing')
			else:
				print(f"  - 🔐 Testing with login_cookie: '{login_cookie}'")
		except Exception as e:
			print(f'❌ Error reading result.json: {e}')
	else:
		print('❌ result.json not found')

	# Check login cookie tracking
	tracking_file = task_folder / 'login_cookie_tracking.json'
	if tracking_file.exists():
		try:
			with open(tracking_file) as f:
				tracking_data = json.load(f)
			print('\n🍪 Login cookie tracking data:')
			print(f'  - Found: {tracking_data.get("found", "N/A")}')
			print(f'  - Step: {tracking_data.get("step", "N/A")}')
			print(f'  - Cookie name: {tracking_data.get("cookie_name", "N/A")}')
			print(f'  - Match type: {tracking_data.get("match_type", "N/A")}')
		except Exception as e:
			print(f'❌ Error reading login_cookie_tracking.json: {e}')
	else:
		print('ℹ️ No login_cookie_tracking.json found (step-by-step tracking not available)')

	# Check storage_state.json
	storage_state_file = task_folder / 'storage_state.json'
	if storage_state_file.exists():
		try:
			with open(storage_state_file) as f:
				storage_state = json.load(f)
			cookies = storage_state.get('cookies', [])
			print(f'\n🍪 Storage state cookies ({len(cookies)} found):')
			for i, cookie in enumerate(cookies[:10]):  # Show first 10
				print(f'  {i + 1}. {cookie.get("name", "unnamed")}')
			if len(cookies) > 10:
				print(f'  ... and {len(cookies) - 10} more')
		except Exception as e:
			print(f'❌ Error reading storage_state.json: {e}')
	else:
		print('ℹ️ No storage_state.json found')

	# Check cookies.json (fallback)
	cookies_file = task_folder / 'cookies.json'
	if cookies_file.exists():
		try:
			with open(cookies_file) as f:
				cookies = json.load(f)
			print(f'\n🍪 Cookies.json ({len(cookies)} found):')
			for i, cookie in enumerate(cookies[:10]):  # Show first 10
				print(f'  {i + 1}. {cookie.get("name", "unnamed")}')
			if len(cookies) > 10:
				print(f'  ... and {len(cookies) - 10} more')
		except Exception as e:
			print(f'❌ Error reading cookies.json: {e}')
	else:
		print('ℹ️ No cookies.json found')

	# Run cookie judge evaluation if login_cookie provided
	if login_cookie:
		print('\n🎯 Running cookie judge evaluation...')
		try:
			result = await evaluate_task_with_login_cookie(login_cookie, task_folder)
			print('📊 Cookie judge result:')
			print(f'  - Success: {result["success"]}')
			print(f'  - Score: {result["score"]}')
			print(f'  - Judgement: {result["judgement"]}')
			if result.get('error'):
				print(f'  - Error: {result["error"]}')
		except Exception as e:
			print(f'❌ Error running cookie judge: {e}')


if __name__ == '__main__':
	if len(sys.argv) < 2:
		print('Usage: python debug_cookie_judge.py <task_id> [login_cookie]')
		sys.exit(1)

	task_id = sys.argv[1]
	login_cookie = sys.argv[2] if len(sys.argv) > 2 else None

	asyncio.run(debug_cookie_judge(task_id, login_cookie))
