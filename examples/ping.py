import asyncio
import sys
import time

import aiohttp


async def ping(session: aiohttp.ClientSession, url: str) -> float:
	"""
	Send a GET request and return the elapsed time in milliseconds.
	If the request fails, returns None.
	"""
	start = time.perf_counter()
	try:
		async with session.get(url) as response:
			# Await the complete response text (optional)
			await response.text()
			status = response.status
	except Exception as e:
		print(f'Failed to request {url}. Reason: {e}')
		return None

	elapsed_ms = (time.perf_counter() - start) * 1000  # Convert to milliseconds
	# print(f'Ping to {url}: {elapsed_ms:.2f} ms (Status Code: {status})')
	return elapsed_ms


async def stress_test(url: str, requests_per_second: int = 200) -> None:
	"""
	Continuously send a batch of parallel requests per second.
	After each batch, log the average response time and estimated throughput.
	"""
	# Using unlimited connections; adjust if necessary
	connector = aiohttp.TCPConnector(limit=0)
	async with aiohttp.ClientSession(connector=connector) as session:
		batch_num = 0
		while True:
			batch_num += 1
			batch_start = time.perf_counter()
			# Launch batch of tasks
			tasks = [asyncio.create_task(ping(session, url)) for _ in range(requests_per_second)]
			results = await asyncio.gather(*tasks)
			batch_elapsed = time.perf_counter() - batch_start

			# Filter out any failed requests (None values)
			valid_results = [res for res in results if res is not None]
			if valid_results:
				avg_response_time = sum(valid_results) / len(valid_results)
			else:
				avg_response_time = float('nan')

			# Throughput: how many requests completed per second in this batch:
			throughput = len(valid_results) / batch_elapsed

			print(
				f'Batch {batch_num}: '
				f'Average Response Time = {avg_response_time:.2f} ms, '
				f'Estimated Throughput = {throughput:.2f} req/sec '
				f'(batch elapsed time: {batch_elapsed:.2f} sec)'
			)

			# Sleep to maintain the desired rate of requests per second
			sleep_time = max(0, 1 - batch_elapsed)
			if sleep_time:
				await asyncio.sleep(sleep_time)


if __name__ == '__main__':
	# Get URL from command-line (default to Google)
	url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.google.com'
	# Optionally get number of requests per second (default: 200)
	try:
		rps = int(sys.argv[2]) if len(sys.argv) > 2 else 200
	except ValueError:
		rps = 200

	asyncio.run(stress_test(url, requests_per_second=rps))
