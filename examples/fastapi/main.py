import asyncio
import logging
import os
from queue import Queue
from threading import Lock
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from browser_use import Agent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create a queue for log messages
log_queue = Queue()
log_lock = Lock()


class LogHandler(logging.Handler):
	def emit(self, record):
		log_entry = self.format(record)
		with log_lock:
			log_queue.put(log_entry)


# Add custom handler to the root logger
root_logger = logging.getLogger('browser_use')
root_logger.addHandler(LogHandler())

app = FastAPI()

# Enable CORS
app.add_middleware(
	CORSMiddleware,
	allow_origins=['*'],  # Allows all origins
	allow_credentials=True,
	allow_methods=['*'],  # Allows all methods
	allow_headers=['*'],  # Allows all headers
)

# Create static directory if it doesn't exist
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
os.makedirs(STATIC_DIR, exist_ok=True)

# Serve static files
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


class TaskRequest(BaseModel):
	task: str


class AgentManager:
	def __init__(self):
		self.agent: Optional[Agent] = None
		self.task: Optional[str] = None
		self._running = False

	def create_agent(self, task: str):
		llm = ChatOpenAI(model='gpt-4o')
		self.agent = Agent(task=task, llm=llm)
		self.task = task
		self._running = False

	def get_agent(self) -> Agent:
		if not self.agent:
			raise ValueError('Agent not initialized')
		return self.agent

	@property
	def is_running(self):
		return self._running

	@is_running.setter
	def is_running(self, value: bool):
		self._running = value


# Create a singleton instance
agent_manager = AgentManager()


@app.get('/')
async def read_root():
	return FileResponse(os.path.join(STATIC_DIR, 'index.html'))


@app.post('/agent/run')
async def run_agent(request: TaskRequest):
	try:
		# Create new agent if task is different or agent doesn't exist
		if not agent_manager.agent or agent_manager.task != request.task:
			agent_manager.create_agent(request.task)

		agent = agent_manager.get_agent()
		agent_manager.is_running = True

		# Run in background task to not block
		asyncio.create_task(agent.run())
		return {'status': 'running', 'task': request.task}
	except Exception as e:
		raise HTTPException(status_code=400, detail=str(e))


@app.post('/agent/pause')
async def pause_agent():
	try:
		agent = agent_manager.get_agent()
		agent.pause()
		return {'status': 'paused'}
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))


@app.post('/agent/resume')
async def resume_agent():
	try:
		agent = agent_manager.get_agent()
		agent.resume()
		return {'status': 'resumed'}
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))


@app.post('/agent/stop')
async def stop_agent():
	try:
		agent = agent_manager.get_agent()
		agent.stop()
		agent_manager.is_running = False
		return {'status': 'stopped'}
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))


@app.get('/agent/status')
async def get_status():
	if not agent_manager.agent:
		return {'status': 'not_created'}

	agent = agent_manager.get_agent()
	if agent._stopped:
		status = 'stopped'
	elif agent._paused:
		status = 'paused'
	elif agent_manager.is_running:
		status = 'running'
	else:
		status = 'ready'

	return {'status': status, 'task': agent_manager.task}


@app.get('/logs')
async def event_stream():
	async def generate():
		while True:
			if not log_queue.empty():
				with log_lock:
					while not log_queue.empty():
						log_entry = log_queue.get()
						yield {'event': 'log', 'data': log_entry}
			await asyncio.sleep(0.1)

	return EventSourceResponse(generate())


if __name__ == '__main__':
	import uvicorn

	uvicorn.run(app, host='0.0.0.0', port=8000)
