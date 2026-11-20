import hashlib
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cached_property

from .config import settings

log = logging.getLogger(__name__)
parent_observation = ContextVar("careerlens_langfuse_parent", default=None)


class Telemetry:
    @cached_property
    def client(self):
        if not settings().telemetry_enabled or not all(
            os.getenv(key) for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
        ):
            return None
        try:
            from langfuse import get_client

            return get_client()
        except Exception:  # noqa: BLE001 - telemetry is best effort
            log.warning("langfuse_initialization_failed")
            return None

    def flush(self):
        if self.client:
            self.call(self.client.flush)

    def call(self, action):
        try:
            return action()
        except Exception:  # noqa: BLE001 - telemetry is best effort
            log.warning("langfuse_operation_failed")
            return None


telemetry = Telemetry()


@contextmanager
def span(name, *, generation=False, metadata=None, **attributes):
    client = telemetry.client
    if client is None:
        yield None
        return
    manager = telemetry.call(
        lambda: client.start_as_current_observation(
            name=name,
            as_type="generation" if generation else "span",
            metadata=attributes if metadata is None else metadata,
            trace_context=parent_observation.get(),
        )
    )
    observation = telemetry.call(manager.__enter__) if manager else None
    if observation is None:
        yield None
        return
    token = parent_observation.set({"trace_id": observation.trace_id, "parent_span_id": observation.id})
    try:
        yield observation
    except Exception as exc:
        error_type = type(exc).__name__
        telemetry.call(lambda: observation.update(level="ERROR", status_message=error_type))
        raise
    finally:
        parent_observation.reset(token)
        telemetry.call(lambda: manager.__exit__(None, None, None))


@contextmanager
def identify(observation, user_id, conversation_id):
    if observation is None:
        yield None
        return
    from langfuse import propagate_attributes

    cfg = settings()
    key = (cfg.trace_salt or cfg.jwt_secret).get_secret_value().encode()
    user = hashlib.blake2b(
        str(user_id).encode(), key=hashlib.sha256(key).digest(), digest_size=16
    ).hexdigest()
    context = propagate_attributes(
        user_id=user,
        session_id=(
            str(conversation_id) if not isinstance(conversation_id, str) else f"{conversation_id}:{user}"
        ),
    )
    telemetry.call(context.__enter__)
    try:
        yield observation.trace_id
    finally:
        telemetry.call(lambda: context.__exit__(None, None, None))
