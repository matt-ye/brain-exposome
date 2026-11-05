#!/usr/bin/env python3
"""GET with retries, for the monthly data-refresh Action. Stdlib only.

The 2026-11 run lost all 8 literature topics and 2 of 3 API sources to HTTPError in
about four seconds. Days later, from the same Actions environment, every one of those
URLs answered 200, so it was a passing failure, and a retry might have saved the month:
the radar's 45-day window overlaps the next run by only ~15 days, so a skipped month
leaves papers that no later run will scan.

The status code was never recorded (callers kept only the exception's type name), so
that run's cause is unknowable. `describe` exists so the next one is not.
"""
import http.client
import time
import urllib.error
import urllib.request

ATTEMPTS = 3
DELAYS = (5, 30)        # seconds before the 2nd and 3rd attempt
MAX_RETRY_AFTER = 120   # a CI job should not sleep for as long as a server asks
# 403 is here on purpose. It is normally permanent, but CDNs also use it for a passing
# block of a shared CI IP, and a monthly job can afford two more polite requests.
RETRY_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}

# What a caller should catch. OSError covers URLError, HTTPError, TimeoutError and a
# connection reset mid-read; HTTPException covers a truncated or malformed response.
FETCH_ERRORS = (OSError, http.client.HTTPException)


def fetch(req, timeout, sleep=time.sleep, opener=urllib.request.urlopen):
    """Body bytes of `req`. Raises the last error once the attempts run out."""
    for attempt in range(ATTEMPTS):
        try:
            with opener(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == ATTEMPTS - 1:
                raise
            delay = _retry_after(e) or DELAYS[attempt]
        except FETCH_ERRORS:
            if attempt == ATTEMPTS - 1:
                raise
            delay = DELAYS[attempt]
        sleep(delay)
    raise AssertionError("unreachable")


def _retry_after(e):
    try:
        return min(int(e.headers.get("Retry-After", "")), MAX_RETRY_AFTER)
    except (AttributeError, TypeError, ValueError):
        return None             # absent, or the HTTP-date form: use our own schedule


def describe(e):
    """Short text for a report: 'HTTP 503 Service Unavailable', not just 'HTTPError'."""
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} {e.reason}"
    if isinstance(e, urllib.error.URLError):
        return f"{type(e).__name__}: {e.reason}"
    return type(e).__name__
