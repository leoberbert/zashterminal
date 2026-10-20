"""Hostname resolve without SIGALRM.

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from zashterminal.utils.security import HostnameValidator


class DnsResolveTests(unittest.TestCase):
    def test_resolve_returns_first_address(self):
        fake = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("203.0.113.10", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake) as getaddrinfo:
            result = HostnameValidator.resolve_hostname("example.test", timeout=2.0)
            self.assertEqual(result, "203.0.113.10")
            getaddrinfo.assert_called()

    def test_resolve_timeout_returns_none(self):
        def hang(*_args, **_kwargs):
            import time

            time.sleep(2.0)
            return []

        with patch("socket.getaddrinfo", side_effect=hang):
            result = HostnameValidator.resolve_hostname("slow.test", timeout=0.05)
            self.assertIsNone(result)

    def test_resolve_gaierror_returns_none(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("boom")):
            self.assertIsNone(HostnameValidator.resolve_hostname("bad.test", timeout=1.0))

    def test_does_not_install_sigalrm(self):
        with (
            patch("socket.getaddrinfo", return_value=[]),
            patch("signal.signal") as signal_fn,
            patch("signal.setitimer") as setitimer_fn,
        ):
            HostnameValidator.resolve_hostname("example.test", timeout=1.0)
            signal_fn.assert_not_called()
            setitimer_fn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
