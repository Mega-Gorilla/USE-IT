"""
Test semaphore functionality, especially multiprocess semaphores.
"""

import asyncio
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest

# Add the browser-use directory to the path so we can import from it
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from browser_use.utils import retry


def worker_acquire_semaphore(
	worker_id: int,
	start_time: float,
	results_queue: multiprocessing.Queue,
	hold_time: float = 0.5,
	timeout: float = 5.0,
	should_release: bool = True,
):
	"""Worker process that tries to acquire a semaphore."""
	try:
		print(f'Worker {worker_id} starting...')

		# Define a function decorated with multiprocess semaphore
		@retry(
			retries=0,
			timeout=10,
			semaphore_limit=3,  # Only 3 concurrent processes allowed
			semaphore_name='test_multiprocess_sem',
			semaphore_scope='multiprocess',
			semaphore_timeout=timeout,
			semaphore_lax=False,  # Strict mode - must acquire semaphore
		)
		async def semaphore_protected_function():
			acquire_time = time.time() - start_time
			results_queue.put(('acquired', worker_id, acquire_time))

			# Hold the semaphore for a bit
			await asyncio.sleep(hold_time)

			release_time = time.time() - start_time
			results_queue.put(('released', worker_id, release_time))
			return f'Worker {worker_id} completed'

		# Run the async function
		print(f'Worker {worker_id} running async function...')
		result = asyncio.run(semaphore_protected_function())
		print(f'Worker {worker_id} completed with result: {result}')
		results_queue.put(('completed', worker_id, result))

	except TimeoutError as e:
		timeout_time = time.time() - start_time
		print(f'Worker {worker_id} timed out: {e}')
		results_queue.put(('timeout', worker_id, timeout_time, str(e)))
	except Exception as e:
		error_time = time.time() - start_time
		print(f'Worker {worker_id} error: {type(e).__name__}: {e}')
		import traceback

		traceback.print_exc()
		results_queue.put(('error', worker_id, error_time, str(e)))


def worker_that_dies(
	worker_id: int,
	start_time: float,
	results_queue: multiprocessing.Queue,
	die_after: float = 0.2,
):
	"""Worker process that acquires semaphore then dies without releasing."""
	try:

		@retry(
			retries=0,
			timeout=10,
			semaphore_limit=2,  # Only 2 concurrent processes
			semaphore_name='test_death_sem',
			semaphore_scope='multiprocess',
			semaphore_timeout=5.0,
			semaphore_lax=False,
		)
		async def semaphore_protected_function():
			acquire_time = time.time() - start_time
			results_queue.put(('acquired', worker_id, acquire_time))

			# Hold for a bit then simulate crash
			await asyncio.sleep(die_after)

			# Simulate unexpected death
			os._exit(1)  # Hard exit without cleanup

		asyncio.run(semaphore_protected_function())

	except Exception as e:
		error_time = time.time() - start_time
		results_queue.put(('error', worker_id, error_time, str(e)))


def worker_death_test_normal(
	worker_id: int,
	start_time: float,
	results_queue: multiprocessing.Queue,
):
	"""Worker for death test that uses the same semaphore."""

	@retry(
		retries=0,
		timeout=10,
		semaphore_limit=2,
		semaphore_name='test_death_sem',
		semaphore_scope='multiprocess',
		semaphore_timeout=5.0,
		semaphore_lax=False,
	)
	async def semaphore_protected_function():
		acquire_time = time.time() - start_time
		results_queue.put(('acquired', worker_id, acquire_time))
		await asyncio.sleep(0.2)
		release_time = time.time() - start_time
		results_queue.put(('released', worker_id, release_time))
		return f'Worker {worker_id} completed'

	try:
		result = asyncio.run(semaphore_protected_function())
		results_queue.put(('completed', worker_id, result))
	except Exception as e:
		error_time = time.time() - start_time
		results_queue.put(('error', worker_id, error_time, str(e)))


class TestMultiprocessSemaphore:
	"""Test multiprocess semaphore functionality."""

	def test_basic_multiprocess_semaphore(self):
		"""Test that semaphore limits work across processes."""
		results_queue = multiprocessing.Queue()
		start_time = time.time()
		processes = []

		# Start 6 worker processes (semaphore limit is 3)
		for i in range(6):
			p = multiprocessing.Process(target=worker_acquire_semaphore, args=(i, start_time, results_queue, 0.5, 5.0))
			p.start()
			processes.append(p)
			time.sleep(0.05)  # Small delay to ensure processes start in order

		# Wait for all processes to complete
		for p in processes:
			p.join(timeout=10)

		# Collect results
		results = []
		while not results_queue.empty():
			results.append(results_queue.get())

		# Analyze results
		acquired_events = [r for r in results if r[0] == 'acquired']
		released_events = [r for r in results if r[0] == 'released']
		completed_events = [r for r in results if r[0] == 'completed']

		# All 6 workers should complete successfully
		assert len(completed_events) == 6, f'Expected 6 completions, got {len(completed_events)}'

		# Sort by acquisition time
		acquired_events.sort(key=lambda x: x[2])

		# Extract worker IDs in order of acquisition
		acquisition_order = [event[1] for event in acquired_events]

		# Since we have a semaphore limit of 3:
		# The first 3 to acquire should be from workers 0, 1, 2 (started first)
		first_three = set(acquisition_order[:3])
		assert first_three == {0, 1, 2}, f'First 3 acquisitions should be workers 0, 1, 2, got {first_three}'

		# The next 3 should be from workers 3, 4, 5 (started later)
		last_three = set(acquisition_order[3:])
		assert last_three == {3, 4, 5}, f'Last 3 acquisitions should be workers 3, 4, 5, got {last_three}'

		# First 3 should acquire quickly (within 1.5s accounting for process startup and Python import overhead)
		for i in range(3):
			assert acquired_events[i][2] < 2, (
				f'Worker {acquired_events[i][1]} should acquire quickly, took {acquired_events[i][2]:.2f}s'
			)

		# Next 3 should wait longer (should take > 0.4s to acquire due to 0.5s hold time)
		for i in range(3, 6):
			assert acquired_events[i][2] > 0.4, (
				f'Worker {acquired_events[i][1]} should wait for semaphore, took {acquired_events[i][2]:.2f}s'
			)

	def test_semaphore_timeout(self):
		"""Test that semaphore timeout works correctly."""
		results_queue = multiprocessing.Queue()
		start_time = time.time()
		processes = []

		# Start 4 workers with short timeout (semaphore limit is 3)
		for i in range(4):
			p = multiprocessing.Process(
				target=worker_acquire_semaphore,
				args=(i, start_time, results_queue, 2.0, 0.5),  # 2s hold, 0.5s timeout
			)
			p.start()
			processes.append(p)

		# Wait for processes
		for p in processes:
			p.join(timeout=5)

		# Collect results
		results = []
		while not results_queue.empty():
			results.append(results_queue.get())

		# Check that we have timeout events
		timeout_events = [r for r in results if r[0] == 'timeout']
		completed_events = [r for r in results if r[0] == 'completed']

		# 3 should complete, 1 should timeout
		assert len(completed_events) == 3, f'Expected 3 completions, got {len(completed_events)}'
		assert len(timeout_events) == 1, f'Expected 1 timeout, got {len(timeout_events)}'

		# Timeout should occur relatively quickly (< 2s)
		assert timeout_events[0][2] < 2.0, f'Timeout should occur within ~0.5s, took {timeout_events[0][2]:.2f}s'

	def test_process_death_releases_semaphore(self):
		"""Test that killing a process releases its semaphore slot."""
		results_queue = multiprocessing.Queue()
		start_time = time.time()

		# Start 2 processes that will die (limit is 2)
		death_processes = []
		for i in range(2):
			p = multiprocessing.Process(target=worker_that_dies, args=(i, start_time, results_queue, 0.3))
			p.start()
			death_processes.append(p)

		# Wait a bit for them to acquire
		time.sleep(0.5)

		# Now start 2 more processes that should be able to acquire after the first 2 die
		normal_processes = []
		for i in range(2, 4):
			p = multiprocessing.Process(target=worker_death_test_normal, args=(i, start_time, results_queue))
			p.start()
			normal_processes.append(p)

		# Wait for death processes to exit
		for p in death_processes:
			p.join(timeout=2)
			assert p.exitcode == 1, f'Process should have exited with code 1, got {p.exitcode}'

		# Wait for normal processes
		for p in normal_processes:
			p.join(timeout=10)
			assert p.exitcode == 0, 'Process should complete successfully'

		# Collect results
		results = []
		while not results_queue.empty():
			results.append(results_queue.get())

		# Check that processes 2 and 3 were able to acquire
		acquired_events = [r for r in results if r[0] == 'acquired']
		completed_events = [r for r in results if r[0] == 'completed' and r[1] >= 2]

		# Should have 4 acquisitions total (2 that died + 2 that completed)
		assert len(acquired_events) >= 4, f'Expected at least 4 acquisitions, got {len(acquired_events)}'

		# Processes 2 and 3 should complete
		assert len(completed_events) == 2, f'Expected 2 completions from workers 2-3, got {len(completed_events)}'

	def test_concurrent_acquisition_order(self):
		"""Test that processes acquire semaphore with fairness."""
		results_queue = multiprocessing.Queue()
		start_time = time.time()
		processes = []

		# Start 5 processes with delays to establish clear order (limit is 2)
		for i in range(5):
			p = multiprocessing.Process(
				target=worker_acquire_semaphore,
				args=(i, start_time, results_queue, 0.3, 5.0),  # 0.3s hold time
			)
			p.start()
			processes.append(p)
			time.sleep(0.1)  # 100ms delay between starts to establish clear order

		# Wait for all to complete
		for p in processes:
			p.join(timeout=10)

		# Collect and analyze results
		results = []
		while not results_queue.empty():
			results.append(results_queue.get())

		acquired_events = [r for r in results if r[0] == 'acquired']
		acquired_events.sort(key=lambda x: x[2])  # Sort by acquisition time

		# Extract worker IDs in order of acquisition
		acquisition_order = [event[1] for event in acquired_events]

		# With a limit of 2 and clear start order:
		# - Workers 0, 1 should acquire first (they started first)
		# - Workers 2, 3, 4 should acquire after 0, 1 release

		# First two should be from the first two started
		first_two = set(acquisition_order[:2])
		assert first_two == {0, 1}, f'First 2 acquisitions should be workers 0 and 1, got {first_two}'

		# The remaining should all eventually acquire
		assert len(acquisition_order) == 5, f'All 5 workers should acquire, got {len(acquisition_order)}'
		assert set(acquisition_order) == {0, 1, 2, 3, 4}, f'All workers should acquire: {acquisition_order}'

	def test_semaphore_persistence_across_runs(self):
		"""Test that semaphore state persists correctly across process runs."""
		results_queue = multiprocessing.Queue()
		start_time = time.time()

		# First run: Start 3 processes that hold semaphore (limit is 3)
		first_batch = []
		for i in range(3):
			p = multiprocessing.Process(
				target=worker_acquire_semaphore,
				args=(i, start_time, results_queue, 1.0, 5.0),  # Hold for 1 second
			)
			p.start()
			first_batch.append(p)

		# Wait for them to acquire and ensure all slots are taken
		time.sleep(0.5)

		# Try to start one more - should timeout quickly
		timeout_worker = multiprocessing.Process(
			target=worker_acquire_semaphore,
			args=(99, start_time, results_queue, 0.5, 0.3),  # Very short timeout
		)
		timeout_worker.start()
		timeout_worker.join(timeout=2)

		# Wait for first batch to complete
		for p in first_batch:
			p.join(timeout=5)

		# Now start a new batch - should work immediately
		second_batch = []
		for i in range(3, 6):
			p = multiprocessing.Process(target=worker_acquire_semaphore, args=(i, start_time, results_queue, 0.2, 5.0))
			p.start()
			second_batch.append(p)

		for p in second_batch:
			p.join(timeout=5)

		# Analyze results
		results = []
		while not results_queue.empty():
			results.append(results_queue.get())

		timeout_events = [r for r in results if r[0] == 'timeout' and r[1] == 99]
		second_batch_acquired = [r for r in results if r[0] == 'acquired' and r[1] >= 3]

		# Worker 99 should timeout
		assert len(timeout_events) == 1, 'Worker 99 should timeout'

		# Second batch should all acquire successfully
		assert len(second_batch_acquired) == 3, 'All second batch workers should acquire'

		# Second batch should acquire after first batch releases
		for event in second_batch_acquired:
			# Should acquire within 4 seconds (1s hold + overhead)
			assert event[2] < 4.0, f'Worker {event[1]} took too long to acquire: {event[2]}s'


class TestRegularSemaphoreScopes:
	"""Test non-multiprocess semaphore scopes still work correctly."""

	async def test_global_scope(self):
		"""Test global scope semaphore."""
		results = []

		@retry(
			retries=0,
			timeout=1,
			semaphore_limit=2,
			semaphore_scope='global',
			semaphore_name='test_global',
		)
		async def test_func(worker_id: int):
			results.append(('start', worker_id, time.time()))
			await asyncio.sleep(0.1)
			results.append(('end', worker_id, time.time()))
			return worker_id

		# Run 4 tasks concurrently (limit is 2)
		tasks = [test_func(i) for i in range(4)]
		await asyncio.gather(*tasks)

		# Check that only 2 ran concurrently
		starts = [r for r in results if r[0] == 'start']
		starts.sort(key=lambda x: x[2])

		# First 2 should start immediately
		assert starts[1][2] - starts[0][2] < 0.05

		# 3rd should wait for first to finish
		assert starts[2][2] - starts[0][2] > 0.08

	async def test_class_scope(self):
		"""Test class scope semaphore."""

		class TestClass:
			def __init__(self):
				self.results = []

			@retry(
				retries=0,
				timeout=1,
				semaphore_limit=1,
				semaphore_scope='class',
				semaphore_name='test_method',
			)
			async def test_method(self, worker_id: int):
				self.results.append(('start', worker_id, time.time()))
				await asyncio.sleep(0.1)
				self.results.append(('end', worker_id, time.time()))
				return worker_id

		# Create two instances
		obj1 = TestClass()
		obj2 = TestClass()

		# Run method on both instances concurrently
		# They should share the semaphore (class scope)
		start_time = time.time()
		await asyncio.gather(
			obj1.test_method(1),
			obj2.test_method(2),
		)
		end_time = time.time()

		# Should take ~0.2s (sequential) not ~0.1s (parallel)
		assert end_time - start_time > 0.18

	async def test_self_scope(self):
		"""Test self scope semaphore."""

		class TestClass:
			def __init__(self):
				self.results = []

			@retry(
				retries=0,
				timeout=1,
				semaphore_limit=1,
				semaphore_scope='self',
				semaphore_name='test_method',
			)
			async def test_method(self, worker_id: int):
				self.results.append(('start', worker_id, time.time()))
				await asyncio.sleep(0.1)
				self.results.append(('end', worker_id, time.time()))
				return worker_id

		# Create two instances
		obj1 = TestClass()
		obj2 = TestClass()

		# Run method on both instances concurrently
		# They should NOT share the semaphore (self scope)
		start_time = time.time()
		await asyncio.gather(
			obj1.test_method(1),
			obj2.test_method(2),
		)
		end_time = time.time()

		# Should take ~0.1s (parallel) not ~0.2s (sequential)
		assert end_time - start_time < 0.15


if __name__ == '__main__':
	# Run the tests
	pytest.main([__file__, '-v'])
