from browser_use import Agent
from browser_use.logging_config import setup_logging

# Set up logging to files
setup_logging(debug_log_file='debug.log', info_log_file='info.log')

Agent('Find the founders of browser-use').run_sync()
