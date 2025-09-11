import argparse
import asyncio
import json
import os
import re
import schedule
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from dateutil import parser
from dateutil.relativedelta import relativedelta

from browser_use import Agent, BrowserSession
from browser_use.llm.google import ChatGoogle

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Browser profile directory for persistence
USER_DATA_DIR = Path.home() / '.config' / 'whatsapp_scheduler' / 'browser_profile'
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Storage state file for cookies
STORAGE_STATE_FILE = USER_DATA_DIR / 'storage_state.json'

async def parse_schedule_file(file_path):
    """Parse the schedule.txt file using LLM to extract scheduling instructions"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Initialize Gemini LLM using browser-use's ChatGoogle
    llm = ChatGoogle(
        model="gemini-2.0-flash-exp",
        temperature=0.1,
        api_key=GEMINI_API_KEY
    )
    
    # Current date and time for context
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    current_day = now.strftime("%A")
    
    prompt = f"""
    You are a scheduling assistant. Parse the following schedule instructions and extract the scheduling information.
    
    Current date: {current_date} ({current_day})
    Current time: {current_time}
    
    Instructions to parse:
    {content}
    
    For each scheduling instruction, extract:
    1. Contact name (who to send to) - extract just the name, not descriptions like "hinge date"
    2. Message content (what to send) - extract the core message/instruction, not the full command
    3. Date and time (when to send)
    
    Rules for parsing:
    - If no year is specified, assume current year
    - If only time is given without date, assume today (if time hasn't passed) or tomorrow (if time has passed)
    - "Next [day]" means the next occurrence of that day (not today)
    - If no time is specified, default to 9:00 AM
    - Format dates as YYYY-MM-DD
    - Format times as HH:MM (24-hour format)
    
    IMPORTANT for message extraction:
    - For "Send happy birthday message to X" → message should be "happy birthday message"
    - For "Tell X that Y" → message should be "Y"
    - For "Remind X to Y" → message should be "Y"
    - For "Send 'actual message' to X" → message should be "actual message"
    - Extract parenthetical names: "hinge date (Camila)" → contact is "Camila"
    
    Return ONLY a JSON array with objects in this format:
    [
        {{
            "contact": "contact_name",
            "message": "message content",
            "date": "YYYY-MM-DD",
            "time": "HH:MM",
            "original_instruction": "the original line from the schedule"
        }}
    ]
    
    Skip any lines that are headers, empty, or start with #.
    """
    
    from browser_use.llm.messages import UserMessage
    response = await llm.ainvoke([UserMessage(content=prompt)])
    # Access the completion attribute which contains the actual response text
    response_content = response.completion if hasattr(response, 'completion') else str(response)
    
    # Extract JSON from response
    import json
    json_match = re.search(r'```json\s*(.*?)\s*```', response_content, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find JSON array directly
        json_str = re.sub(r'^[^\[]*', '', response_content)
        json_str = re.sub(r'[^\]]*$', '', json_str)
    
    try:
        parsed_items = json.loads(json_str)
    except json.JSONDecodeError:
        print(f"Error parsing LLM response: {response_content}")
        return []
    
    # Convert parsed items to schedule format
    schedules = []
    for item in parsed_items:
        try:
            # Parse date and time
            date_str = item['date']
            time_str = item['time']
            scheduled_time = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            
            schedules.append({
                'contact': item['contact'],
                'message': item['message'],
                'scheduled_time': scheduled_time,
                'original_instruction': item.get('original_instruction', '')
            })
        except Exception as e:
            print(f"Error processing schedule item: {item}, Error: {e}")
            continue
    
    return schedules

async def compose_message_if_needed(message, contact, scheduled_date=None):
    """Use LLM to compose a proper message if the input is a description"""
    # Only keep very simple, complete messages as-is
    simple_greetings = ['hi', 'hello', 'hey', 'bye', 'goodbye', 'thanks', 'thank you', 'yes', 'no', 'ok', 'okay']
    
    # If it's a simple greeting or already well-formed, keep it
    if message.lower().strip() in simple_greetings:
        return message
    
    # Otherwise, let the LLM decide if it needs composition
    llm = ChatGoogle(
        model="gemini-2.0-flash-exp",
        temperature=0.7,
        api_key=GEMINI_API_KEY
    )
    
    # Add date context for time-sensitive messages
    date_context = ""
    if scheduled_date:
        date_context = f"\nThis message will be sent on: {scheduled_date.strftime('%A, %B %d, %Y')}"
    
    prompt = f"""
    You need to prepare a WhatsApp message based on this input: "{message}"
    The message is for: {contact}{date_context}
    
    Rules:
    1. If the input is already a complete, well-formed message -> return it exactly as is
    2. If it's a description or instruction -> compose a proper message
    3. Fix any perspective issues (e.g., "I miss her" when sending TO someone should be "I miss you")
    4. For reminders like "pick up the car", write a complete reminder message
    5. For birthday/celebration messages, be warm and use appropriate emojis
    6. If the instruction mentions a future day but the message is being sent ON that day, adjust to say "today"
    
    Examples:
    - "Hi" -> "Hi"
    - "I miss her" (to Camila) -> "I miss you ❤️"
    - "pick up the car" -> "Hey! Just a reminder to pick up the car today 🚗"
    - "happy birthday message" -> "Happy birthday! 🎉 Wishing you an amazing day..."
    
    Return ONLY the message text, nothing else.
    """
    
    from browser_use.llm.messages import UserMessage
    response = await llm.ainvoke([UserMessage(content=prompt)])
    composed_message = response.completion if hasattr(response, 'completion') else str(response)
    return composed_message.strip()

async def send_whatsapp_message(contact, message, compose_message=True, scheduled_date=None):
    """Use browser-use to send a WhatsApp message"""
    try:
        # Optionally compose a proper message
        if compose_message:
            final_message = await compose_message_if_needed(message, contact, scheduled_date)
            print(f"Composed message: {final_message}")
        else:
            final_message = message
        
        # Initialize Gemini LLM for browser control
        llm = ChatGoogle(
            model="gemini-2.0-flash-exp",
            temperature=0.3,
            api_key=GEMINI_API_KEY
        )
        
        # Define the task for the agent
        task = f"""
        You are helping to send a WhatsApp message. Follow these steps:
        
        1. Navigate to https://web.whatsapp.com
        2. Wait for the page to load completely
        3. If you see a QR code, scan it with your phone to log in
        4. Once logged in, search for the contact named "{contact}" in the search box
        5. Click on the contact to open the conversation
        6. Type the following message in the message input field: "{final_message}"
        7. Send the message by pressing Enter or clicking the send button
        8. Confirm the message was sent successfully
        
        Be patient with page loads and make sure each action completes before proceeding to the next.
        """
        
        # Initialize browser session with persistent user data and storage state
        browser_session = BrowserSession(
            headless=False,  # Show browser for WhatsApp Web
            user_data_dir=str(USER_DATA_DIR),  # Use persistent profile directory
            storage_state=str(STORAGE_STATE_FILE) if STORAGE_STATE_FILE.exists() else None  # Use saved cookies/session
        )
        
        # Create and run agent
        agent = Agent(
            task=task,
            llm=llm,
            browser_session=browser_session,
        )
        
        result = await agent.run()
        print(f"Message sent to {contact}: {final_message}")
        
        return True
        
    except Exception as e:
        print(f"Error sending message to {contact}: {str(e)}")
        return False

async def schedule_messages(schedules, no_immediate=False):
    """Schedule all messages using the schedule library"""
    immediate_tasks = []
    
    for item in schedules:
        scheduled_time = item['scheduled_time']
        contact = item['contact']
        message = item['message']
        
        # Check if the time is in the past - if so, run immediately
        if scheduled_time <= datetime.now():
            if no_immediate:
                print(f"⏭️ Skipping past message (--no-immediate): Send '{message}' to {contact}")
            else:
                print(f"⚡ Past time detected - running immediately: Send '{message}' to {contact}")
                # Add to immediate tasks list
                immediate_tasks.append(send_whatsapp_message(contact, message, scheduled_date=scheduled_time))
        else:
            # Schedule for future
            # Format time for scheduling
            time_str = scheduled_time.strftime("%H:%M")
            
            # Create a wrapper function for the async call
            def create_job(c, m, date):
                def job():
                    asyncio.run(send_whatsapp_message(c, m, scheduled_date=date))
                return job
            
            # Schedule based on the date
            if scheduled_time.date() == datetime.now().date():
                # Today
                schedule.every().day.at(time_str).do(create_job(contact, message, scheduled_time))
                print(f"Scheduled for today at {time_str}: Send '{message}' to {contact}")
            else:
                # Future date - we'll need to check daily
                def check_and_send(c, m, target_date):
                    def job():
                        if datetime.now().date() == target_date.date():
                            asyncio.run(send_whatsapp_message(c, m, scheduled_date=target_date))
                            return schedule.CancelJob
                    return job
                
                schedule.every().day.at(time_str).do(check_and_send(contact, message, scheduled_time))
                print(f"Scheduled for {scheduled_time.strftime('%Y-%m-%d %H:%M')}: Send '{message}' to {contact}")
    
    # Run all immediate tasks sequentially (not parallel)
    if immediate_tasks:
        print(f"\n🚀 Running {len(immediate_tasks)} immediate messages sequentially...")
        for i, task in enumerate(immediate_tasks, 1):
            print(f"📱 Sending message {i}/{len(immediate_tasks)}...")
            try:
                await task
                print(f"✅ Message {i} sent successfully")
            except Exception as e:
                print(f"❌ Error sending message {i}: {e}")
                continue

async def main(test_mode=False, no_immediate=False):
    """Main function to run the WhatsApp scheduler"""
    if not GEMINI_API_KEY:
        print("❌ Error: GEMINI_API_KEY environment variable is required")
        print("Please set it with: export GEMINI_API_KEY='your-api-key-here'")
        return
        
    print("🚀 WhatsApp Scheduler Starting...")
    print(f"📁 Browser profile directory: {USER_DATA_DIR}")
    print(f"🍪 Storage state file: {STORAGE_STATE_FILE}")
    
    # Parse schedule file
    schedule_file = Path("schedule.txt")
    if not schedule_file.exists():
        print("Error: schedule.txt not found!")
        return
    
    print("📄 Parsing schedule file...")
    schedules = await parse_schedule_file(schedule_file)
    print("✅ Schedule file parsed successfully")
    
    if not schedules:
        print("No valid schedules found in schedule.txt")
        return
    
    print(f"\nFound {len(schedules)} scheduled messages:")
    for item in schedules:
        print(f"  - {item['scheduled_time'].strftime('%Y-%m-%d %H:%M')}: "
              f"Send '{item['message']}' to {item['contact']}")
    
    if test_mode:
        print("\n=== TEST MODE - Extracted Schedule Data ===")
        
        # Compose messages for test display
        print("Composing full messages...")
        test_data = []
        for s in schedules:
            composed_msg = await compose_message_if_needed(s['message'], s['contact'], s['scheduled_time'])
            test_data.append({
                'contact': s['contact'],
                'original_message': s['message'],
                'composed_message': composed_msg,
                'scheduled_time': s['scheduled_time'].strftime('%Y-%m-%d %H:%M'),
                'original_instruction': s['original_instruction']
            })
        
        print(json.dumps(test_data, indent=2))
        print("\nTest mode complete. Exiting without scheduling messages.")
        return
    
    # Schedule all messages
    await schedule_messages(schedules, no_immediate)
    
    print("\nScheduler is running. Press Ctrl+C to stop.")
    print("Note: On first run, you'll need to scan the WhatsApp Web QR code.")
    
    # Keep the scheduler running
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)  # Check every 30 seconds
    except KeyboardInterrupt:
        print("\nScheduler stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='WhatsApp Scheduler - Send scheduled messages via WhatsApp Web')
    parser.add_argument('--test', '-t', action='store_true', help='Test mode: parse schedule and show messages without sending')
    parser.add_argument('--no-immediate', action='store_true', help='Skip running past-due messages immediately')
    args = parser.parse_args()
    
    asyncio.run(main(test_mode=args.test, no_immediate=args.no_immediate))