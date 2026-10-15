"""Server-owned HTTP root spans, parented to SDK inference boundaries."""

import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def _safe(action):
    try:
        return action()
    except Exception:
        # Never log exception text: exporter failures may include credentials.
        try:
            logger.warning("Runtime tracing operation failed; original provider behavior preserved")
        except Exception:
            pass
        return None


@contextmanager
def request_span(tracer, request_id, method, route):
    """Activate a metadata-only root span; never record raw exception messages.

    Route must be a fixed route template, not a URL containing user data.
    Caller supplies only server-generated request IDs.
    """
    span = None
    context = None
    if tracer is not None:
        span = _safe(
            lambda: tracer.start_span(
                "mem0.http",
                attributes={
                    "langfuse.observation.type": "span",
                    "mem0.request_id": request_id,
                    "http.request.method": method,
                    "http.route": route,
                },
            )
        )
        if span is not None:

            def activate():
                from opentelemetry.trace import use_span

                active = use_span(span, end_on_exit=False, record_exception=False, set_status_on_exception=False)
                active.__enter__()
                return active

            context = _safe(activate)
    try:
        yield span
    finally:
        if context is not None:
            _safe(lambda: context.__exit__(None, None, None))
        if span is not None:
            _safe(span.end)


def http_status(span, code):
    if span is not None:
        _safe(lambda: span.set_attribute("http.response.status_code", code))
