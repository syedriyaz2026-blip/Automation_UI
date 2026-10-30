class PortalLayoutChange(RuntimeError):
    """Raised when a page contract is not satisfied."""
    def __init__(self, message: str, screenshot: str = ""):
        super().__init__(message)
        self.screenshot = screenshot
