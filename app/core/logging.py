import json
import logging
import sys
from datetime import datetime, timezone


_STDLIB_ATTRS = logging.LogRecord.__dict__.keys() | {
    "message", "asctime", "args", "exc_info", "exc_text", "stack_info",
    "msg", "name", "levelname", "levelno", "pathname", "filename",
    "module", "lineno", "funcName", "created", "msecs", "relativeCreated",
    "thread", "threadName", "processName", "process", "taskName",
}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Append any extra fields passed via `extra={...}`
        for key, value in record.__dict__.items():
            if key not in _STDLIB_ATTRS:
                log[key] = value
        if record.exc_info:
            log["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log)


def setup_logging(log_level: str = "INFO") -> None:
    level = logging.getLevelName(log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    logging.basicConfig(level=level, handlers=[handler], force=True)

    # Suppress uvicorn.access — our middleware handles request logging
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.error").propagate = True
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
