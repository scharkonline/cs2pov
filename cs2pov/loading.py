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
# ANSI Color Codes
# =============================================================================

DIM = "\033[2m"      # Dim/faint text
RESET = "\033[0m"    # Reset all attributes
CLEAR_LINE = "\033[2K\r"  # Clear line and return to start


class LoadingAnimation:
    """Animated loading message that oscillates periods.

    Usage:
        with LoadingAnimation():
            do_slow_work()

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

    def _animate(self):
        """Animation loop running in background thread."""
        dots = self.min_dots
        direction = 1  # 1 = increasing, -1 = decreasing

        while not self._stop_event.is_set():
            # Build the display string
            dot_str = "." * dots
            display = f"{DIM}{self.message}{dot_str}{RESET}"

            # Clear line and print (no newline)
            sys.stderr.write(f"{CLEAR_LINE}{display}")
            sys.stderr.flush()

            # Update dot count
            dots += direction
            if dots >= self.max_dots:
                direction = -1
            elif dots <= self.min_dots:
                direction = 1

            # Wait before next frame
            self._stop_event.wait(0.3)

        # Clear the line when done
        sys.stderr.write(CLEAR_LINE)
        sys.stderr.flush()

    def start(self):
        """Start the animation."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the animation and clear the line."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
