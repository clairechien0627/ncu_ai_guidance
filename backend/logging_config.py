import logging.config
import os


def configure_logging() -> None:
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_format = os.environ.get("LOG_FORMAT", "json").lower()

    if log_format == "text":
        formatter: dict = {
            "()": "logging.Formatter",
            "format": "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        }
    else:
        formatter = {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        }

    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": formatter},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"level": log_level, "handlers": ["console"]},
        "loggers": {
            "alembic":        {"level": "WARNING", "propagate": True},
            "uvicorn.access": {"level": "INFO",    "propagate": True},
            "uvicorn.error":  {"level": "INFO",    "propagate": True},
        },
    })
