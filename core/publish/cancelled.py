"""User cancelled an in-flight publish (multi-platform or single upload)."""


class PublishCancelled(Exception):
    """Raised when the user requests cancel during upload/processing."""
