import contextlib
import json
import pathlib
import tempfile
import typing
import unittest
from unittest import mock

import urllib3
from agents.nix import discovery


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return 10000.0 + self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def response(
    status: int = 200,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, typing.Any] | None = None,
) -> urllib3.HTTPResponse:
    return urllib3.HTTPResponse(
        status=status,
        headers=headers,
        body=json.dumps(payload or {"total_count": 0, "items": []}).encode(),
    )


def item(repo: str, path: str = "marketplace.json") -> dict[str, typing.Any]:
    return {"repository": {"full_name": repo}, "path": path}


class DiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            mock.patch.dict(discovery.os.environ, {"SEARCH_BUDGET_SECONDS": ""})
        )
        self.clock = Clock()
        for name in ("monotonic", "time", "sleep"):
            stack.enter_context(
                mock.patch.object(
                    discovery.time, name, getattr(self.clock, name)
                )
            )
        self.http = urllib3.PoolManager(
            headers={"Authorization": "Bearer test"}
        )
        self.request = stack.enter_context(
            mock.patch.object(self.http, "request")
        )
        self.temp = stack.enter_context(tempfile.TemporaryDirectory())
        self.out = pathlib.Path(self.temp) / "nested" / "scan.json"

    def test_code_requests_and_retries_are_paced(self) -> None:
        self.request.side_effect = [response(503), response(), response()]
        discovery.get(self.http, "code", "query", 1)
        discovery.get(self.http, "code", "query", 2)
        self.assertEqual(self.clock.sleeps, [8, 8, 8])
        for call in self.request.call_args_list:
            self.assertIs(call.kwargs["retries"], False)
            self.assertEqual(
                call.kwargs["headers"]["Authorization"], "Bearer test"
            )

    def test_primary_limit_waits_past_reset(self) -> None:
        reset = int(self.clock.time() + 75)
        self.request.side_effect = [
            response(
                403,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset),
                },
            ),
            response(),
        ]
        discovery.get(self.http, "code", "query", 1)
        self.assertEqual(self.clock.time(), reset + 1)
        self.assertEqual(self.request.call_count, 2)

    def test_secondary_limit_uses_increasing_minute_waits(self) -> None:
        limited = response(403, payload={"message": "Secondary rate limit"})
        self.request.side_effect = [limited, limited, response()]
        discovery.get(self.http, "code", "query", 1)
        self.assertEqual(self.clock.sleeps, [8, 60, 120])

    def test_retry_after_is_honored(self) -> None:
        self.request.side_effect = [
            response(429, headers={"Retry-After": "45"}),
            response(),
        ]
        discovery.get(self.http, "code", "query", 1)
        self.assertEqual(self.clock.sleeps, [8, 45])

    def test_permanent_auth_errors_are_not_retried(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status):
                self.request.reset_mock()
                self.request.return_value = response(
                    status, payload={"message": "Resource not accessible"}
                )
                with self.assertRaisesRegex(OSError, f"HTTP {status}"):
                    discovery.get(self.http, "code", "query", 1)
                self.request.assert_called_once()

    def test_transport_retry_keeps_request_pacing(self) -> None:
        self.request.side_effect = [
            urllib3.exceptions.ProtocolError("connection dropped"),
            response(),
        ]
        discovery.get(self.http, "code", "query", 1)
        self.assertEqual(self.clock.sleeps, [8, 8])
        self.assertEqual(self.request.call_count, 2)

    def test_exhausted_transport_retry_becomes_oserror(self) -> None:
        error = urllib3.exceptions.MaxRetryError(
            urllib3.HTTPConnectionPool("github.com"), "/search/code"
        )
        self.request.side_effect = error
        with self.assertRaises(OSError) as raised:
            discovery.get(self.http, "code", "query", 1)
        self.assertIs(raised.exception.__cause__, error)
        self.assertEqual(self.request.call_count, 6)
        self.assertTrue(all(delay >= 8 for delay in self.clock.sleeps))

    def test_deadline_blocks_initial_request(self) -> None:
        with self.assertRaises(TimeoutError):
            discovery.get(
                self.http, "code", "query", 1, deadline=self.clock.now + 8
            )
        self.request.assert_not_called()
        self.assertEqual(self.clock.sleeps, [])

    def test_deadline_blocks_retry_before_waiting(self) -> None:
        self.request.return_value = response(429, headers={"Retry-After": "60"})
        with self.assertRaises(TimeoutError):
            discovery.get(
                self.http, "code", "query", 1, deadline=self.clock.now + 68
            )
        self.request.assert_called_once()
        self.assertEqual(self.clock.sleeps, [8])

    def test_code_budget_spans_queries_and_preserves_partial_results(
        self,
    ) -> None:
        self.request.return_value = response(
            payload={"total_count": 1, "items": [item("Org/First")]}
        )
        with (
            mock.patch.dict(
                discovery.os.environ, {"SEARCH_BUDGET_SECONDS": "16"}
            ),
            self.assertRaises(TimeoutError),
        ):
            discovery.write_scan(
                self.out,
                "github-code",
                discovery.code(self.http, ["first", "second"]),
            )
        self.request.assert_called_once()
        self.assertEqual(self.clock.sleeps, [8])
        self.assertEqual(
            json.loads(self.out.read_text())["repositories"],
            ["github:org/first"],
        )

    def test_failed_later_page_preserves_sorted_unique_results(self) -> None:
        self.request.side_effect = [
            response(
                payload={
                    "total_count": 101,
                    "items": [item("Z/Repo"), item("A/Repo"), item("z/REPO")],
                }
            ),
            response(401),
        ]
        with self.assertRaises(OSError):
            discovery.write_scan(
                self.out, "github-code", discovery.code(self.http, ["query"])
            )
        self.assertEqual(
            json.loads(self.out.read_text()),
            {
                "site": "github-code",
                "repositories": ["github:a/repo", "github:z/repo"],
            },
        )

    def test_page_checkpoint_precedes_next_page_failure(self) -> None:
        items = [
            item(f"Org/Repo-{index:03}") for index in range(discovery.PAGE)
        ]
        expected = sorted(f"github:org/repo-{index:03}" for index in range(100))

        def request(
            _method: str, _url: str, **kwargs: typing.Any
        ) -> urllib3.HTTPResponse:
            if kwargs["fields"]["page"] == "1":
                return response(payload={"total_count": 101, "items": items})
            self.assertEqual(
                json.loads(self.out.read_text())["repositories"], expected
            )
            return response(401)

        self.request.side_effect = request
        with self.assertRaises(OSError):
            discovery.write_scan(
                self.out, "github-code", discovery.code(self.http, ["query"])
            )
        self.assertEqual(self.request.call_count, 2)

    def test_sharding_pagination_and_path_filename_filters(self) -> None:
        path = ".codex/marketplace.json"
        self.request.side_effect = [
            response(payload={"total_count": 1001}),
            response(
                payload={
                    "total_count": 101,
                    "items": [
                        item("Org/First", path),
                        item("Org/WrongPath", "marketplace.json"),
                        item("Org/WrongName", ".codex/plugin.json"),
                        {"path": path},
                    ],
                }
            ),
            response(
                payload={
                    "total_count": 101,
                    "items": [item("Org/Second", path)],
                }
            ),
            response(payload={"total_count": 1001}),
            response(),
            response(
                payload={"total_count": 1, "items": [item("Org/Third", path)]}
            ),
        ]
        self.assertEqual(
            list(
                discovery.code(
                    self.http,
                    ["query"],
                    paths={path, ".codex/plugin.json"},
                    filenames={"marketplace.json"},
                )
            ),
            ["github:Org/First", "github:Org/Second", "github:Org/Third"],
        )
        self.assertEqual(
            [
                (call.kwargs["fields"]["q"], call.kwargs["fields"]["page"])
                for call in self.request.call_args_list
            ],
            [
                ("query size:>=0", "1"),
                ("query size:0..7", "1"),
                ("query size:0..7", "2"),
                ("query size:>=8", "1"),
                ("query size:8..15", "1"),
                ("query size:>=16", "1"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
