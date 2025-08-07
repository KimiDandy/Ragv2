import logging
import sys

def setup_logging():
    """
    Configures the root logger for the application.

    This setup provides a consistent, colorful, and informative logging format
    across the entire application. It logs to the standard output.
    """
    # Define a custom format with color for better readability
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )

    # The default logger in FastAPI/Uvicorn is already configured.
    # To avoid conflicts and duplicate handlers, we first remove the default handler.
    # Then we add our custom Loguru-intercepted handler.
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Configure Loguru to use this format
    from loguru import logger
    logger.remove()
    logger.add(sys.stdout, colorize=True, format=log_format)

    print("INFO:     Logging configured successfully.")


class InterceptHandler(logging.Handler):
    """
    Custom logging handler to intercept standard logging messages
    and redirect them to Loguru.
    """
    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        from loguru import logger
        logger.opt(depth=depth, exception=record.exc_info).log(
            level,
            record.getMessage(),
        )

# We will also need to add loguru to our dependencies.
# I will handle this later when we finalize the project setup.
