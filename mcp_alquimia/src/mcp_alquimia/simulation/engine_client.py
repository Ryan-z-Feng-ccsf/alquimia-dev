"""Synchronous, single-session subprocess client. No MCP or CFFI dependency."""

import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


class EngineError(RuntimeError):
    """The base exception for all engine-related errors, tracking the associated log location."""
    
    def __init__(self, message, log_path: str | None):
        super().__init__(message)
        self.log_path = log_path

class EngineProcessError(RuntimeError):
    """A native error, timeout, or engine process crash with its engine log location."""


class EngineCommandError(EngineProcessError):
    """The engine process is alive and returned a structured error."""


class AlquimiaEngineProcess:
    """Own one persistent native process; use as a context manager.

    Calls are sequential. All engine output goes to engine_output.log; structured
    replies arrive through a separate inherited pipe on POSIX systems.

    Attributes:
        timeout (float): Maximum seconds to wait for each engine process response.
        run_dir (Path): Absolute working directory containing the staged inputs.
        log_path (Path): File receiving native stdout and stderr.
        process (subprocess.Popen): Handle to the persistent native engine process.
    """

    def __init__(self, executable: str | Path, run_dir: str | Path, timeout: float = 60.0):
        """Start the engine process and wait for its ready response.

        Args:
            executable (str or Path): Path to the precompiled native engine process.
            run_dir (str or Path): Existing directory containing staged inputs.
                Its engine_output.log file must not already exist.
            timeout (float): Positive, finite response timeout in seconds.

        Raises:
            ValueError: The timeout or startup response is invalid.
            OSError: The log, pipes, or executable cannot be opened.
            EngineProcessError: Native startup fails, times out, or exits unexpectedly.
        """
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.timeout = timeout
        self.run_dir = Path(run_dir).resolve()
        self.log_path = self.run_dir / "engine_output.log"
        self._buffer = b""
        self._closed = False
        
        # stdout stuffed with irrelevant info
        # Start a new pipe for subprocess to write
        # Process to read
        # Process close the read end, keep the write end
        read_fd, write_fd = os.pipe()
        
        try:
            # Exclusive bin writing
            with self.log_path.open("xb") as log:
                # C function entry alquimia_engine_process.c
                self.process = subprocess.Popen(
                    [str(Path(executable).resolve()), "--response-fd", str(write_fd)],
                    cwd=self.run_dir,   # Working directory: benchmark/batch_chem/
                    stdin=subprocess.PIPE,  # Process write through self.process.stdin.write()
                    stdout=log,
                    stderr=subprocess.STDOUT,   # Redirect stdout, stderr to the log file
                    pass_fds=(write_fd,),   # Keep the write fd for subprocess, close read fd
                    # Detach from Process group (calls setsid).
                    # Prevents terminal signals like SIGINT (Ctrl+C) from propagating 
                    # to the child, allowing it to run safely as a background daemon.
                    start_new_session=True,
                )
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            # Prevent dead lock
            os.close(write_fd)
        
        self._read_fd = read_fd
        # Initialize an OS-optimal I/O multiplexer (e.g., epoll on Linux, kqueue on macOS)
        self._selector = selectors.DefaultSelector()
        # Register the read file descriptor for READ events.
        # This allows the parent process to poll for data asynchronously without blocking.
        self._selector.register(read_fd, selectors.EVENT_READ)
        try:
            if self._receive() != "ready":
                raise EngineProcessError(f"Unexpected startup reply; log: {self.log_path}")
        except BaseException:
            self._dispose()
            raise

    def _receive(self):
        """Read one structured reply from the dedicated response pipe.

        Returns:
            object: Decoded JSON value from the successful reply's result field.

        Raises:
            EngineCommandError: The engine process reports an operation failure.
            EngineProcessError: The response times out, exceeds the size limit, or the
                engine process closes the pipe before completing it.
            ValueError: The reply is not valid JSON or has an invalid envelope.
            KeyError: A required response field is missing.
            OSError: Reading or waiting on the pipe fails.
        """
        deadline = time.monotonic() + self.timeout
        # Continue reading until a newline character is found in the buffer.
        # (This indicates a complete JSON message has been received)
        
        # If the deadline has passed, or the selector polls until the timeout 
        # without detecting any 'ready to read' events, raise a timeout error.
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            
            # If the deadline has passed, or the selector polls until the timeout 
            # without detecting any 'ready to read' events, raise a timeout error.
            # Sleep at selector, will not occupy CPU
            if remaining <= 0 or not self._selector.select(remaining):
                raise EngineProcessError(f"Engine process timed out; log: {self.log_path}")
            
            # At this point, selector.select() was awakened by an OS-level read event.
            # The pipe is guaranteed to have data, so os.read() will NOT block.
            # Read up to 64KB (65536 bytes) in a single chunk.
            chunk = os.read(self._read_fd, 65536)
            
            # If the selector indicates readiness but os.read() returns empty bytes (b""),
            # it means the write end of the pipe was closed (i.e., the subprocess died).
            if not chunk:
                raise EngineProcessError(
                    f"Engine process exited without a response (status {self.process.poll()}); "
                    f"log: {self.log_path}")
                
            # Append the newly read data chunk to our buffer.
            self._buffer += chunk
            
            # Prevent the buffer from growing indefinitely if the subprocess goes rogue 
            # and never sends a newline. Cap the maximum single-message size at 8 MiB.
            if len(self._buffer) > 8 * 1024 * 1024:
                raise EngineProcessError(f"Engine process response exceeds 8 MiB; log: {self.log_path}")
            
        json_line, self._buffer = self._buffer.split(b"\n", 1)
        reply = json.loads(json_line)
        
        # It MUST be a JSON object (dict) and MUST contain a boolean 'success' field.
        if not isinstance(reply, dict) or type(reply.get("success")) is not bool:
            raise ValueError("Invalid engine process response envelope")
        if not reply["success"]:
            error = reply["error"]
            raise EngineCommandError(
                f"{error['operation']} failed ({error['code']}): {error['message']}; "
                f"log: {self.log_path}")
        return reply["result"]

    def request(self, operation, **parameters):
        """Call an engine process operation and return its structured result.

        Failures after sending a request close the engine process to prevent reuse of
        uncertain engine state. Serialization errors leave it available.

        Args:
            operation (str): Native operation: setup, initialize, react,
                get_results, or close. Prefer close() for complete cleanup.
            **parameters: JSON-compatible operation arguments. For react, dt is
                the positive step duration in seconds and max_steps the number of
                steps (1 to 10000) the engine runs before replying.

        Returns:
            object: Decoded result, such as setup metadata or a state snapshot.

        Raises:
            EngineProcessError: The engine process is closed, an operation fails, or process
                communication fails. Native failures include the engine log path.
            ValueError: Parameters contain non-finite numbers or exceed the
                request size limit.
            TypeError: Parameters cannot be serialized as JSON.
        """
        if self._closed:
            raise EngineProcessError("Engine process is closed")
        
        # To str
        payload = json.dumps({"operation": operation, **parameters}, allow_nan=False)
        
        # JSON (16KB)
        if len(payload.encode()) + 1 >= 16384:
            raise ValueError("Engine process request exceeds protocol size limit")
        try:
            self.process.stdin.write((payload + "\n").encode())
            self.process.stdin.flush()
            return self._receive()
        except EngineCommandError:
            # Give native code a chance to release the engine and finalize MPI.
            # Never replace the original operation error with a cleanup error.
            # Prevent infinite recursion
            if operation != "close":
                try:
                    self.close()
                except Exception:
                    self._dispose()
            raise
        except BaseException as error:
            self._dispose()
            if isinstance(error, (OSError, ValueError, KeyError)):
                raise EngineProcessError(f"Engine process communication failed; log: {self.log_path}") from error
            raise

    def _dispose(self):
        """Terminate a live engine process, reap it, and close pipes; safe to repeat."""
        if self._closed:
            return
        self._closed = True
        
        # Check if the process is still alive (poll() returns None if running)
        if self.process.poll() is None:
            # Graceful Shutdown
            try:
                # This ensures we kill the engine AND any child threads/processes it spawned, preventing orphans.
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                # The process might have exited just milliseconds before we sent the signal.
                pass
            
            # Force Kill
            try:
                # Give the process group 2 seconds to clean up its own resources and exit gracefully.
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    # If it hangs or refuses to exit, send SIGKILL.
                    # This cannot be ignored; the OS will wipe it from memory immediately.
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.process.wait()
        self.process.stdin.close()
        self._selector.close()
        os.close(self._read_fd)

    def close(self):
        """Shut down Alquimia, reap the engine process, and release its pipes.

        Repeated calls are harmless. Process cleanup runs even if native
        shutdown fails or the engine process does not exit within the timeout.

        Raises:
            EngineProcessError: Shutdown or communication fails, or the engine process exits
                with a nonzero status.
            subprocess.TimeoutExpired: The engine process acknowledges shutdown but
                does not exit within the configured timeout.
        """
        if self._closed:
            return
        try:
            self.request("close")
            # subprocess is C. Success returns 0
            code = self.process.wait(timeout=self.timeout)
            if code:
                raise EngineProcessError(f"Engine process exited with status {code}; log: {self.log_path}")
        finally:
            self._dispose()

    def __enter__(self):
        """Enter the context for the already-started engine process.

        Returns:
            AlquimiaEngineProcess: This engine process instance.
        """
        return self

    def __exit__(self, exc_type, exc, traceback):
        """Close the engine process without masking an exception from the context body.

        Args:
            exc_type (type or None): Type of the active exception, if any.
            exc (BaseException or None): Active exception instance.
            traceback (types.TracebackType or None): Active exception traceback.

        Raises:
            EngineProcessError: Cleanup fails when no context-body exception is active.
            subprocess.TimeoutExpired: Shutdown times out when no context-body
                exception is active.
        """
        if exc_type:
            try:
                self.close()
            except Exception:
                self._dispose()
        else:
            self.close()
