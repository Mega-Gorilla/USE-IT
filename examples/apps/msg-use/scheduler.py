#!/usr/bin/env python3
"""
WhatsApp Message Scheduler - Send scheduled messages via WhatsApp Web
"""
import argparse
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path


def setup_environment(debug: bool):
	if not debug:
		os.environ['BROWSER_USE_SETUP_LOGGING'] = 'false'
		os.environ['BROWSER_USE_LOGGING_LEVEL'] = 'critical'
		logging.getLogger().setLevel(logging.CRITICAL)
	else:
		os.environ['BROWSER_USE_SETUP_LOGGING'] = 'true'
		os.environ['BROWSER_USE_LOGGING_LEVEL'] = 'info'


parser = argparse.ArgumentParser(description='WhatsApp Scheduler - Send scheduled messages via WhatsApp Web')
parser.add_argument('--debug', action='store_true', help='Debug mode: show browser and verbose logs')
parser.add_argument('--test', action='store_true', help='Test mode: show what messages would be sent without sending them')
args = parser.parse_args()
setup_environment(args.debug)

from browser_use import Agent, BrowserSession
from browser_use.llm.google import ChatGoogle

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')

USER_DATA_DIR = Path.home() / '.config' / 'whatsapp_scheduler' / 'browser_profile'
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_STATE_FILE = USER_DATA_DIR / 'storage_state.json'


async def parse_messages():
	"""Parse messages.txt and extract scheduling info"""
	messages_file = Path('messages.txt')
	if not messages_file.exists():
		print("❌ messages.txt not found!")
		return []
	
	with open(messages_file, 'r') as f:
		content = f.read()
	
	llm = ChatGoogle(
		model='gemini-2.0-flash-exp',
		temperature=0.1,
		api_key=GOOGLE_API_KEY
	)
	
	now = datetime.now()
	prompt = f"""
	Parse these WhatsApp message instructions and extract:
	1. Contact name (extract just the name, not descriptions)
	2. Message content (what to send)
	3. Date and time (when to send)
	
	Current date/time: {now.strftime('%Y-%m-%d %H:%M')}
	Today is: {now.strftime('%Y-%m-%d')}
	Current time is: {now.strftime('%H:%M')}
	
	Instructions:
	{content}
	
	Return ONLY a JSON array with format:
	[{{"contact": "name", "message": "text", "datetime": "YYYY-MM-DD HH:MM"}}]
	
	CRITICAL: Transform instructions into actual messages:
	
	QUOTED TEXT → Use exactly as-is:
	- Text in "quotes" becomes the exact message
	
	UNQUOTED INSTRUCTIONS → Generate actual content:
	- If it's an instruction to write something → write the actual thing
	- If it's an instruction to tell someone something → write what to tell them
	- If it's an instruction to remind someone → write the actual reminder
	
	DO NOT copy the instruction - create the actual message content!
	
	Time Rules:
	- If only time given (like "at 15:30"), use TODAY 
	- If no date specified, assume TODAY
	- If no year given, use current year  
	- Default time is 9:00 if not specified
	- Extract names from parentheses: "hinge date (Camila)" → "Camila"
	- "tomorrow" means {(now + timedelta(days=1)).strftime('%Y-%m-%d')}
	- "next tuesday" or similar means the next occurrence of that day
	"""
	
	from browser_use.llm.messages import UserMessage
	response = await llm.ainvoke([UserMessage(content=prompt)])
	response_text = response.completion if hasattr(response, 'completion') else str(response)
	
	# Extract JSON
	json_match = re.search(r'\[.*?\]', response_text, re.DOTALL)
	if json_match:
		try:
			return json.loads(json_match.group())
		except:
			pass
	return []


async def send_message(contact, message):
	"""Send a WhatsApp message"""
	print(f"\n📱 Sending to {contact}: {message}")
	
	llm = ChatGoogle(
		model='gemini-2.0-flash-exp',
		temperature=0.3,
		api_key=GOOGLE_API_KEY
	)
	
	task = f"""
	Send WhatsApp message:
	1. Go to https://web.whatsapp.com
	2. Search for contact: {contact}
	3. Click on the contact
	4. Type message: {message}
	5. Press Enter to send
	6. Confirm sent
	"""
	
	browser = BrowserSession(
		headless=not args.debug,  # headless=False only when debug=True
		user_data_dir=str(USER_DATA_DIR),
		storage_state=str(STORAGE_STATE_FILE) if STORAGE_STATE_FILE.exists() else None
	)
	
	agent = Agent(task=task, llm=llm, browser_session=browser)
	await agent.run()
	print(f"✅ Sent to {contact}")


async def main():
	if not GOOGLE_API_KEY:
		print("❌ Set GOOGLE_API_KEY or GEMINI_API_KEY environment variable")
		return
	
	print("WhatsApp Scheduler")
	print(f"Profile: {USER_DATA_DIR}")
	print()
	
	# Parse messages
	print("Parsing messages.txt...")
	messages = await parse_messages()
	
	if not messages:
		print("No messages found")
		return
	
	print(f"\nFound {len(messages)} messages:")
	for msg in messages:
		print(f"  • {msg['datetime']}: {msg['message'][:30]}... to {msg['contact']}")
	
	# Separate past and future messages
	now = datetime.now()
	immediate = []
	future = []
	
	for msg in messages:
		msg_time = datetime.strptime(msg['datetime'], '%Y-%m-%d %H:%M')
		if msg_time <= now:
			immediate.append(msg)
		else:
			future.append(msg)
	
	# Test mode - just show what would be sent
	if args.test:
		print(f"\n=== TEST MODE - Preview ===")
		if immediate:
			print(f"\nWould send {len(immediate)} past-due messages NOW:")
			for msg in immediate:
				print(f"  📱 To {msg['contact']}: {msg['message']}")
		if future:
			print(f"\nWould monitor {len(future)} future messages:")
			for msg in future:
				print(f"  ⏰ {msg['datetime']}: To {msg['contact']}: {msg['message']}")
		print("\nTest mode complete. No messages sent.")
		return
	
	# Send immediate messages
	if immediate:
		print(f"\nSending {len(immediate)} past-due messages NOW...")
		for msg in immediate:
			await send_message(msg['contact'], msg['message'])
	
	# Monitor future messages
	if future:
		print(f"\n⏰ Monitoring {len(future)} future messages...")
		print("Press Ctrl+C to stop.\n")
		
		while future:
			now = datetime.now()
			due = []
			remaining = []
			
			for msg in future:
				msg_time = datetime.strptime(msg['datetime'], '%Y-%m-%d %H:%M')
				if msg_time <= now:
					due.append(msg)
				else:
					remaining.append(msg)
			
			# Send due messages
			for msg in due:
				print(f"\n⏰ Time reached for {msg['contact']}")
				await send_message(msg['contact'], msg['message'])
			
			future = remaining
			
			if future:
				# Show next message
				next_msg = min(future, key=lambda x: datetime.strptime(x['datetime'], '%Y-%m-%d %H:%M'))
				print(f"Next: {next_msg['datetime']} to {next_msg['contact']}")
				await asyncio.sleep(30)  # Check every 30 seconds
	
	print("\n✅ All messages processed!")


if __name__ == '__main__':
	asyncio.run(main())