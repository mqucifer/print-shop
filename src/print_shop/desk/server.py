"""The desk's HTTP server: the standard library's, bound to 127.0.0.1 only, answering GET.

Its routes:
- `/`: what's on the printer, and the job list;
- `/job/<id>`: one job's record;
- `/job/<id>/answers.json`: its answers file, to save for `--answers`;
- `/job/<id>/frame/<n>`: its `n`th camera frame (from 0), by its number in the ledger;
- `/job/<id>/log`: its `job.log`, as text.

Anything else is a 404, and every method but GET and HEAD is refused: the desk has nothing to
send anything to.
"""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from print_shop.desk.again import answers_file
from print_shop.desk.page import index_page, job_page, not_found_page
from print_shop.desk.read import Desk

__all__ = ["HOST", "make_server"]

#: The only address it listens on: the Mac itself.
HOST = "127.0.0.1"

_JOB = re.compile(r"^/job/([^/]+)(?:/(answers\.json|log|frame/(\d+)))?/?$")


def make_server(desk: Desk, port: int) -> ThreadingHTTPServer:
    """The server for `desk` on 127.0.0.1 at `port` (0 picks a free one). Not started."""

    class Handler(_Handler):
        pass

    Handler.desk = desk
    return ThreadingHTTPServer((HOST, port), Handler)


class _Handler(BaseHTTPRequestHandler):
    desk: Desk
    server_version = "print-desk"

    def do_GET(self) -> None:  # noqa: N802 - the standard library's name
        self._answer(head=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._answer(head=True)

    def _refuse(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_PUT = do_DELETE = do_PATCH = _refuse  # noqa: N815

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # a page view isn't worth a line in the terminal

    def _answer(self, head: bool) -> None:
        path = unquote(urlsplit(self.path).path)
        if path == "/":
            body = index_page(self.desk.on_hand(), self.desk.jobs(), self.desk.ledger_path)
            return self._send(body.encode(), "text/html; charset=utf-8", head)
        found = _JOB.match(path)
        if found is None:
            return self._missing(f"Nothing is at {path}.", head)
        job_id, what, frame = found.group(1), found.group(2), found.group(3)
        if what is None:
            record = self.desk.job(job_id)
            if record is None:
                return self._missing(f"No job {job_id} in the ledger or the jobs folder.", head)
            return self._send(job_page(record).encode(), "text/html; charset=utf-8", head)
        if what == "log":
            log = self.desk.log_path(job_id)
            if log is None:
                return self._missing(f"Job {job_id} has no log.", head)
            return self._send(log.read_bytes(), "text/plain; charset=utf-8", head)
        if frame is not None:
            image = self.desk.frame_path(job_id, int(frame))
            if image is None:
                return self._missing(f"Job {job_id} has no frame {frame}.", head)
            return self._send(image.read_bytes(), "image/jpeg", head)
        record = self.desk.job(job_id)
        answers = answers_file(record.row) if record is not None and record.row else None
        if answers is None:
            return self._missing(f"Job {job_id} has no answers to replay.", head)
        return self._send(
            json.dumps(answers, indent=2).encode(), "application/json", head,
            download=f"answers-{job_id[:8]}.json",
        )

    def _missing(self, what: str, head: bool) -> None:
        self._send(not_found_page(what).encode(), "text/html; charset=utf-8", head,
                   code=HTTPStatus.NOT_FOUND)

    def _send(self, body: bytes, kind: str, head: bool, code: int = HTTPStatus.OK,
              download: str | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if download is not None:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        if not head:
            self.wfile.write(body)
