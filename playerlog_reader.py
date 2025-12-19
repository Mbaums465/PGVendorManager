"""
PlayerLogReader - Standardized log file reader for Project Gorgon tools

This module provides a reusable class for incrementally reading the Player.log file.
It handles:
- Tracking read position between calls
- Detecting file resets (when the game deletes and recreates the file)
- Efficient incremental reading of only new content
- Both line-by-line iteration and full content reading

Usage:
    from playerlog_reader import PlayerLogReader
    
    # Create reader instance
    reader = PlayerLogReader(log_path)
    
    # Option 1: Get new lines as a list
    new_lines = reader.read_new_lines()
    for line in new_lines:
        process(line)
    
    # Option 2: Get new content as a single string (for multi-line pattern matching)
    content = reader.read_new_content()
    
    # Option 3: Iterate over new lines directly
    for line in reader.iter_new_lines():
        process(line)
    
    # Reset to re-read entire file
    reader.reset()
    
    # Get current state (for persistence if needed)
    state = reader.get_state()
    # Later restore state
    reader.set_state(state)
"""

import os
from typing import List, Iterator, Optional, Tuple, Dict, Any
from dataclasses import dataclass


@dataclass
class LogReaderState:
    """State object for the log reader, can be serialized for persistence."""
    position: int
    file_size: int
    
    def to_dict(self) -> Dict[str, int]:
        return {'position': self.position, 'file_size': self.file_size}
    
    @classmethod
    def from_dict(cls, data: Dict[str, int]) -> 'LogReaderState':
        return cls(position=data.get('position', 0), file_size=data.get('file_size', 0))


class PlayerLogReader:
    """
    Standardized incremental reader for Project Gorgon Player.log files.
    
    Handles the common patterns needed across multiple PG tools:
    - Tracks last read position to only read new content
    - Detects file resets (when game restarts and truncates the log)
    - Provides multiple reading modes (lines, content, iterator)
    
    Thread Safety:
        This class is NOT thread-safe. If using in a multi-threaded context,
        wrap calls with appropriate locking.
    
    Attributes:
        log_path: Path to the Player.log file
        encoding: File encoding (default: 'utf-8')
        errors: How to handle encoding errors (default: 'ignore')
    """
    
    def __init__(
        self,
        log_path: str,
        encoding: str = 'utf-8',
        errors: str = 'ignore'
    ):
        """
        Initialize the PlayerLogReader.
        
        Args:
            log_path: Full path to the Player.log file
            encoding: File encoding (default: 'utf-8')
            errors: Error handling for encoding issues (default: 'ignore')
        """
        self.log_path = log_path
        self.encoding = encoding
        self.errors = errors
        
        # Internal state
        self._position: int = 0
        self._last_file_size: int = 0
    
    def _check_file_status(self) -> Tuple[bool, int]:
        """
        Check the current file status and detect resets.
        
        Returns:
            Tuple of (file_exists, current_size)
            Also updates internal state if file was reset.
        """
        if not os.path.exists(self.log_path):
            return False, 0
        
        try:
            current_size = os.path.getsize(self.log_path)
        except (OSError, IOError):
            return False, 0
        
        # Detect file reset: if file size decreased, the game restarted
        # and created a new log file - we need to read from the beginning
        if current_size < self._last_file_size:
            self._position = 0
            self._last_file_size = 0
        
        return True, current_size
    
    def has_new_content(self) -> bool:
        """
        Check if there is new content to read without actually reading it.
        
        Returns:
            True if there is new content available, False otherwise.
        """
        exists, current_size = self._check_file_status()
        if not exists:
            return False
        
        return current_size > self._position
    
    def read_new_content(self) -> str:
        """
        Read all new content since the last read as a single string.
        
        This is useful when you need to do multi-line pattern matching
        (e.g., parsing Word of Power discoveries that span multiple lines).
        
        Returns:
            String containing all new content, or empty string if no new content.
        """
        exists, current_size = self._check_file_status()
        if not exists:
            return ""
        
        # No new content
        if current_size <= self._position:
            return ""
        
        try:
            with open(self.log_path, 'r', encoding=self.encoding, errors=self.errors) as f:
                f.seek(self._position)
                content = f.read()
                self._position = f.tell()
                self._last_file_size = current_size
                return content
        except (OSError, IOError):
            return ""
    
    def read_new_lines(self) -> List[str]:
        """
        Read all new lines since the last read as a list.
        
        Returns:
            List of new lines (with newlines stripped), or empty list if no new content.
        """
        content = self.read_new_content()
        if not content:
            return []
        
        # Split into lines, handling both \n and \r\n
        lines = content.splitlines()
        return lines
    
    def iter_new_lines(self) -> Iterator[str]:
        """
        Iterate over new lines one at a time.
        
        This is a generator that yields each new line. It reads all new content
        first, then yields lines one by one.
        
        Yields:
            Individual lines (with newlines stripped).
        """
        lines = self.read_new_lines()
        for line in lines:
            yield line
    
    def read_new_lines_streaming(self) -> Iterator[str]:
        """
        Read new lines in a streaming fashion without loading all content into memory.
        
        This is more memory-efficient for very large log files, but note that
        the position is updated after each line, so if you stop iterating early,
        the remaining lines will still be "consumed" for position tracking purposes.
        
        Yields:
            Individual lines (with newlines stripped).
        """
        exists, current_size = self._check_file_status()
        if not exists:
            return
        
        # No new content
        if current_size <= self._position:
            return
        
        try:
            with open(self.log_path, 'r', encoding=self.encoding, errors=self.errors) as f:
                f.seek(self._position)
                for line in f:
                    yield line.rstrip('\n\r')
                self._position = f.tell()
                self._last_file_size = current_size
        except (OSError, IOError):
            return
    
    def reset(self) -> None:
        """
        Reset the reader to start from the beginning of the file.
        
        Call this to re-read the entire file from the start.
        """
        self._position = 0
        self._last_file_size = 0
    
    def get_state(self) -> LogReaderState:
        """
        Get the current reader state for persistence.
        
        Returns:
            LogReaderState object containing current position and file size.
        """
        return LogReaderState(position=self._position, file_size=self._last_file_size)
    
    def set_state(self, state: LogReaderState) -> None:
        """
        Restore a previously saved state.
        
        Args:
            state: LogReaderState object from a previous get_state() call.
        """
        self._position = state.position
        self._last_file_size = state.file_size
    
    def set_position(self, position: int) -> None:
        """
        Manually set the read position.
        
        Args:
            position: Byte position in the file to read from next.
        """
        self._position = position
    
    @property
    def position(self) -> int:
        """Current read position in the file."""
        return self._position
    
    @property
    def last_file_size(self) -> int:
        """Last known file size."""
        return self._last_file_size
    
    def __repr__(self) -> str:
        return f"PlayerLogReader(path='{self.log_path}', position={self._position})"


# Convenience function for simple one-shot reading
def read_log_incremental(
    log_path: str,
    state: Optional[LogReaderState] = None
) -> Tuple[List[str], LogReaderState]:
    """
    Convenience function for stateless incremental log reading.
    
    This is useful when you don't want to maintain a reader instance
    and prefer to pass state explicitly.
    
    Args:
        log_path: Path to the Player.log file
        state: Previous state, or None to read from beginning
    
    Returns:
        Tuple of (list of new lines, new state to save for next call)
    
    Example:
        # First call (reads entire file)
        lines, state = read_log_incremental(log_path)
        
        # Subsequent calls (reads only new content)
        lines, state = read_log_incremental(log_path, state)
    """
    reader = PlayerLogReader(log_path)
    if state:
        reader.set_state(state)
    
    lines = reader.read_new_lines()
    new_state = reader.get_state()
    
    return lines, new_state


# Default log path for Windows installations
DEFAULT_LOG_PATH = os.path.expandvars(
    r'C:\Users\%USERNAME%\AppData\LocalLow\Elder Game\Project Gorgon\Player.log'
)


if __name__ == '__main__':
    # Simple test/demo
    import time
    
    print(f"Testing PlayerLogReader with: {DEFAULT_LOG_PATH}")
    reader = PlayerLogReader(DEFAULT_LOG_PATH)
    
    print(f"Initial state: {reader.get_state()}")
    print(f"Has new content: {reader.has_new_content()}")
    
    # Read initial content
    lines = reader.read_new_lines()
    print(f"Read {len(lines)} lines on initial read")
    print(f"After read state: {reader.get_state()}")
    
    # Check for more content
    print(f"Has new content after read: {reader.has_new_content()}")
    
    # Monitor for new lines (Ctrl+C to stop)
    print("\nMonitoring for new lines (Ctrl+C to stop)...")
    try:
        while True:
            new_lines = reader.read_new_lines()
            if new_lines:
                print(f"Got {len(new_lines)} new lines")
                for line in new_lines[:3]:  # Show first 3
                    print(f"  {line[:80]}...")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped monitoring")
