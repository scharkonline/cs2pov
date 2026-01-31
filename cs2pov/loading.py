"""Loading animation with CS-themed messages."""

import random
import sys
import threading
import time

# =============================================================================
# Loading Messages (add more here!)
# =============================================================================

LOADING_MESSAGES = [
    "Bingo bango bongo",
    "Bish bash bosh",
    "Fire in the hole",
    "It's going to explode",
    "Inhuman reactions",
    "How does he do this",
    "Trying to build pyramids",
    "This is not FPL",
    "The flames come in",
    "Starting to get nervous",
    "Refusing to surrender",
]

# =============================================================================
# ANSI Escape Codes
# =============================================================================

DIM = "\033[2m"           # Dim/faint text
RESET = "\033[0m"         # Reset all attributes
CLEAR_LINE = "\033[2K\r"  # Clear line and return to start
CURSOR_UP = "\033[1A"     # Move cursor up one line


class LoadingAnimation:
    """Animated loading message that oscillates periods.

    The animation stays on the last line while regular output prints above it.

    Usage:
        with LoadingAnimation():
            print("This prints above the animation")
            do_slow_work()
            print("So does this")

    Or manually:
        loader = LoadingAnimation()
        loader.start()
        do_slow_work()
        loader.stop()
    """

    def __init__(self, message: str | None = None, min_dots: int = 1, max_dots: int = 5):
        """Initialize the loading animation.

        Args:
            message: Custom message, or None to pick randomly from LOADING_MESSAGES
            min_dots: Minimum number of dots (default 1)
            max_dots: Maximum number of dots (default 5)
        """
        self.message = message or random.choice(LOADING_MESSAGES)
        self.min_dots = min_dots
        self.max_dots = max_dots
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._active = False
        self._current_dots = min_dots
        self._stderr = None  # Will hold reference to original stderr

    def _draw(self):
        """Draw the current animation state."""
        dot_str = "." * self._current_dots
        display = f"{DIM}{self.message}{dot_str}{RESET}"
        self._stderr.write(f"{CLEAR_LINE}{display}")
        self._stderr.flush()

    def _animate(self):
        """Animation loop running in background thread."""
        direction = 1  # 1 = increasing, -1 = decreasing

        while not self._stop_event.is_set():
            with self._lock:
                if self._active:
                    self._draw()

                    # Update dot count
                    self._current_dots += direction
                    if self._current_dots >= self.max_dots:
                        direction = -1
                    elif self._current_dots <= self.min_dots:
                        direction = 1

            # Wait before next frame
            self._stop_event.wait(0.3)

        # Clear the line when done
        self._stderr.write(CLEAR_LINE)
        self._stderr.flush()

    def print(self, *args, **kwargs):
        """Print while coordinating with the animation.

        Use this instead of print() when you need output to appear above the animation.
        """
        with self._lock:
            if self._active:
                # Clear animation line, move up, then print will go there
                self._stderr.write(CLEAR_LINE)
                self._stderr.flush()

            # Use the original print
            print(*args, **kwargs)

            if self._active:
                # Redraw animation on the new line
                self._draw()

    def start(self):
        """Start the animation."""
        self._stop_event.clear()
        self._current_dots = self.min_dots
        self._stderr = sys.stderr
        self._active = True

        # Install our print wrapper
        import builtins
        self._original_print = builtins.print
        builtins.print = self._wrapped_print

        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def _wrapped_print(self, *args, **kwargs):
        """Wrapped print function that coordinates with animation."""
        with self._lock:
            if self._active:
                # Clear animation line
                self._stderr.write(CLEAR_LINE)
                self._stderr.flush()

            # Call original print
            self._original_print(*args, **kwargs)

            # Flush to ensure output appears before animation redraws
            file = kwargs.get('file', sys.stdout)
            if hasattr(file, 'flush'):
                file.flush()

            if self._active:
                # Redraw animation
                self._draw()

    def stop(self):
        """Stop the animation and clear the line."""
        with self._lock:
            self._active = False

        self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

        # Restore original print
        import builtins
        if hasattr(self, '_original_print'):
            builtins.print = self._original_print

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
