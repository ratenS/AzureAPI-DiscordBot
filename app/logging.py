import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any
from enum import Enum
from pathlib import Path
from uuid import UUID

import orjson
import structlog


class OrjsonRenderer:
    @staticmethod
    def _default(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (Path, UUID)):
            return str(value)
        if is_dataclass(value) and not isinstance(value, type):
            return asdict(value)
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return list(value)
        return str(value)

    def __call__(self, _, __, event_dict):
        return orjson.dumps(event_dict, default=self._default).decode("utf-8")


def configure_logging(level: str) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            OrjsonRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), stream=sys.stdout)
