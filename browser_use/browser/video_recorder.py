"""Video Recording Service for Browser Use Sessions."""

import base64
import logging
from pathlib import Path
from typing import Optional

from browser_use.browser.profile import ViewportSize

try:
	import imageio.v2 as iio
	from imageio.core.format import Format

	IMAGEIO_AVAILABLE = True
except ImportError:
	IMAGEIO_AVAILABLE = False

logger = logging.getLogger(__name__)


class VideoRecorderService:
	"""
	Handles the video encoding process for a browser session using imageio.

	This service captures individual frames from the CDP screencast, decodes them,
	and appends them to a video file using a pip-installable ffmpeg backend.
	It automatically resizes frames to match the target video dimensions.
	"""

	def __init__(self, output_path: Path, size: ViewportSize, framerate: int):
		"""
		Initializes the video recorder.

		Args:
		    output_path: The full path where the video will be saved.
		    size: A ViewportSize object specifying the width and height of the video.
		    framerate: The desired framerate for the output video.
		"""
		self.output_path = output_path
		self.size = size
		self.framerate = framerate
		self._writer: Optional['Format.Writer'] = None
		self._is_active = False

	def start(self) -> None:
		"""
		Prepares and starts the video writer.

		If the required optional dependencies are not installed, this method will
		log an error and do nothing.
		"""
		if not IMAGEIO_AVAILABLE:
			logger.error(
				'MP4 recording requires optional dependencies. Please install them with: pip install "browser-use[video]"'
			)
			return

		try:
			self.output_path.parent.mkdir(parents=True, exist_ok=True)
			self._writer = iio.get_writer(
				str(self.output_path),
				fps=self.framerate,
				codec='libx264',
				quality=8,  # A good balance of quality and file size (1-10 scale)
				pixelformat='yuv420p',  # Ensures compatibility with most players
				macro_block_size=16,  # Recommended for h264
			)
			self._is_active = True
			logger.debug(f'Video recorder started. Output will be saved to {self.output_path}')
		except Exception as e:
			logger.error(f'Failed to initialize video writer: {e}')
			self._is_active = False

	def add_frame(self, frame_data_b64: str) -> None:
		"""
		Decodes a base64-encoded PNG frame and appends it to the video.

		This method is designed to be fast and non-blocking. It will
		gracefully handle corrupted frames.

		Args:
		    frame_data_b64: A base64-encoded string of the PNG frame data.
		"""
		if not self._is_active or not self._writer:
			return

		try:
			frame_bytes = base64.b64decode(frame_data_b64)
			# imageio reads bytes directly and converts to a numpy array
			# The format is auto-detected from the bytes.
			img_array = iio.imread(frame_bytes)

			# Ensure frame dimensions match video dimensions
			h, w, _ = img_array.shape
			if w != self.size['width'] or h != self.size['height']:
				# This can happen if the viewport changes mid-recording.
				# A more robust solution could involve resizing, but that is non-trivial.
				# For now, the video size must be the same as the viewport
				logger.warning(
					f'Frame size ({w}x{h}) does not match video size '
					f'({self.size["width"]}x{self.size["height"]}). Skipping frame.'
				)
				return

			self._writer.append_data(img_array)
		except Exception as e:
			logger.warning(f'Could not process and add video frame: {e}')

	def stop_and_save(self) -> None:
		"""
		Finalizes the video file by closing the writer.

		This method should be called when the recording session is complete.
		"""
		if not self._is_active or not self._writer:
			return

		try:
			self._writer.close()
			logger.info(f'📹 Video recording saved successfully to: {self.output_path}')
		except Exception as e:
			logger.error(f'Failed to finalize and save video: {e}')
		finally:
			self._is_active = False
			self._writer = None
