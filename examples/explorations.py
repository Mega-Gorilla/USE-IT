import asyncio
import gc
import logging
import os
import time

import psutil
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from browser_use import Agent

logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s - %(levelname)s - %(message)s',
	handlers=[logging.StreamHandler(), logging.FileHandler('memory_usage.log')],
)

logger = logging.getLogger(__name__)


def log_current_memory():
	process = psutil.Process(os.getpid())
	memory_mb = process.memory_info().rss / 1024 / 1024
	logger.info(f'Current memory usage: {memory_mb:.2f} MB')
	return memory_mb


async def monitor_idle_memory(duration_seconds: int = 60, interval_seconds: int = 1):
	end_time = time.time() + duration_seconds
	while time.time() < end_time:
		log_current_memory()
		await asyncio.sleep(interval_seconds)


def memory_intensive_operation():
	print('Starting memory-intensive operation...')

	# Create a large list to capture a lot of memory
	large_list = [0] * 10_000_000  # List with 10 million integers

	print('Memory-intensive operation complete. Large list created.')
	time.sleep(2)  # Pause to observe memory usage

	# Release the memory by deleting the list
	del large_list
	print('Large list deleted.')

	# Force garbage collection
	time.sleep(2)  # Pause to observe memory usage


async def main():
	load_dotenv()

	# Log initial memory
	logger.info('=== Initial Memory ===')
	initial_memory = log_current_memory()

	# Run the agent

	logger.info('\n=== Running Agent ===')

	for i in range(10):
		logger.info(f'=== Running Agent {i} ===')

		llm = ChatOpenAI(model='gpt-4o', temperature=0.0)
		task = 'Find the founders of browser-use and draft them a short personalized message'

		async with Agent(task=task, llm=llm) as agent:
			await agent.run()
			# Log post-run memory
			logger.info(f'\n=== Post-Run Memory {i} ===')
			post_run_memory = log_current_memory()

			gc.collect()

	# memory_intensive_operation()

	# Log post-run memory
	logger.info('\n=== Post-Run Memory ===')
	post_run_memory = log_current_memory()

	# Force garbage collection
	gc.collect()

	logger.info('\n=== After GC Memory ===')
	post_gc_memory = log_current_memory()

	# Monitor idle period
	logger.info('\n=== Starting Idle Monitoring (60 seconds) ===')
	await monitor_idle_memory(duration_seconds=60)  # Monitor for 1 minute

	# Final memory check
	logger.info('\n=== Final Memory ===')
	final_memory = log_current_memory()

	# Print summary
	logger.info('\n=== Memory Usage Summary ===')
	logger.info(f'Initial Memory: {initial_memory:.2f} MB')
	logger.info(f'Post-Run Memory: {post_run_memory:.2f} MB')
	logger.info(f'Post-GC Memory: {post_gc_memory:.2f} MB')
	logger.info(f'Final Memory: {final_memory:.2f} MB')
	logger.info(f'Total Change: {final_memory - initial_memory:.2f} MB')


if __name__ == '__main__':
	asyncio.run(main())
