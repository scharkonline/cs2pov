"""Custom exceptions for CS2 POV recorder."""


class CS2POVError(Exception):
    """Base exception for CS2 POV recorder."""

    pass


class DemoNotFoundError(CS2POVError):
    """Demo file does not exist."""

    pass


class PlayerNotFoundError(CS2POVError):
    """Target player not found in demo."""

    pass


class GPUNotAvailableError(CS2POVError):
    """No Vulkan-capable GPU detected."""

    pass


class CS2NotFoundError(CS2POVError):
    """CS2 installation not found."""

    pass


class CS2LaunchError(CS2POVError):
    """Failed to launch CS2."""

    pass


class CaptureError(CS2POVError):
    """FFmpeg or display capture failed."""

    pass


class DemoParseError(CS2POVError):
    """Failed to parse demo file."""

    pass
