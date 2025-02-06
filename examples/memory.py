import asyncio
import gc
import logging
import resource

# Set up logging (both console and file output are enabled)
logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s - %(levelname)s - %(message)s',
	handlers=[logging.StreamHandler(), logging.FileHandler('memory_usage_simple.log')],
)
logger = logging.getLogger(__name__)


def log_current_memory(context: str = '') -> float:
	# ru_maxrss returns KB on Unix; convert to MB.
	memory_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
	# Label the log line with a clear marker "CurrentMemory:" for easy extraction:
	logger.info(f'{context}CurrentMemory: {memory_mb:.2f} MB')
	return memory_mb


async def memory_intensive_task(task_id: int):
	logger.info(f'Task {task_id}: Starting memory allocation.')
	# Allocate a large list to increase memory usage.
	data = [0] * 10_000_000  # List with 10 million integers.
	logger.info(f'Task {task_id}: Memory allocated, list size: {len(data)}')

	# Simulate some async work.
	await asyncio.sleep(1)

	logger.info(f'Task {task_id}: Releasing allocated memory.')
	# Delete the large data structure to free memory.
	del data
	gc.collect()  # Force garbage collection.

	await asyncio.sleep(1)
	log_current_memory(f'Task {task_id}: ')


async def main():
	# Log initial memory usage.
	logger.info('=== Initial Memory ===')
	initial_memory = log_current_memory('Initial: ')

	# Run a series of batches of asynchronous tasks.
	batches = 5  # Define the number of batches.
	tasks_per_batch = 5  # Number of concurrent tasks in each batch.

	for batch in range(batches):
		logger.info(f'=== Batch {batch} starting ===')

		tasks = [asyncio.create_task(memory_intensive_task(task_id)) for task_id in range(tasks_per_batch)]
		await asyncio.gather(*tasks)

		gc.collect()  # Additional garbage collection after a batch.
		logger.info(f'=== Batch {batch} complete ===')
		log_current_memory(f'After Batch {batch}: ')

		await asyncio.sleep(2)  # Pause between batches.

	# Final memory check.
	final_memory = log_current_memory('Final: ')

	# Log summary.
	logger.info('=== Memory Usage Summary ===')
	logger.info(f'Initial Memory: {initial_memory:.2f} MB')
	logger.info(f'Final Memory: {final_memory:.2f} MB')
	logger.info(f'Total Change: {final_memory - initial_memory:.2f} MB')


if __name__ == '__main__':
	asyncio.run(main())
