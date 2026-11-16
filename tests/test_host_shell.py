import os
import subprocess
import unittest
from unittest.mock import patch

from zashterminal.utils import host_shell


class HostShellTests(unittest.TestCase):
    def setUp(self):
        host_shell._enabled = None
        host_shell._host_shell = None
        self.addCleanup(setattr, host_shell, "_enabled", None)
        self.addCleanup(setattr, host_shell, "_host_shell", None)

    def test_wrap_forwards_only_terminal_variables(self):
        env = {
            "TERM": "xterm-256color", "COLORTERM": "truecolor",
            "ZASHTERMINAL_BASH_INIT": "/tmp/x/.rc", "ZDOTDIR": "/tmp/z",
            "PATH": "/container/bin", "HOME": "/home/u",
        }
        cmd = host_shell.wrap_command(["/bin/bash", "--rcfile", "/tmp/x/.rc", "-i"], env)
        self.assertEqual(cmd[:2], ["distrobox-host-exec", "env"])
        self.assertEqual(cmd[-4:], ["/bin/bash", "--rcfile", "/tmp/x/.rc", "-i"])
        forwarded = set(cmd[2:-4])
        self.assertEqual(forwarded, {"TERM=xterm-256color", "COLORTERM=truecolor",
                                     "ZASHTERMINAL_BASH_INIT=/tmp/x/.rc", "ZDOTDIR=/tmp/z"})

    def test_enabled_only_in_distrobox_with_shared_tmp_and_no_opt_out(self):
        def enabled(in_box=True, tool=True, shared=True, opt_out=False):
            host_shell._enabled = None
            env = {host_shell.OPT_OUT_ENV: "1"} if opt_out else {}
            with patch.object(host_shell, "in_distrobox", return_value=in_box), \
                    patch.object(host_shell.shutil, "which", return_value="/x" if tool else None), \
                    patch.object(host_shell, "_tmp_is_shared", return_value=shared), \
                    patch.dict(os.environ, env):
                return host_shell.host_shell_enabled()

        self.assertTrue(enabled())
        self.assertFalse(enabled(in_box=False))
        self.assertFalse(enabled(tool=False))
        self.assertFalse(enabled(shared=False))
        self.assertFalse(enabled(opt_out=True))

    def test_host_shell_falls_back_when_probe_fails(self):
        with patch.object(host_shell, "_run_on_host", return_value=None):
            self.assertEqual(host_shell.host_user_shell(), "/bin/sh")
        host_shell._host_shell = None
        with patch.object(host_shell, "_run_on_host", return_value="/usr/bin/zsh"):
            self.assertEqual(host_shell.host_user_shell(), "/usr/bin/zsh")

    def test_in_distrobox_needs_container_id_and_containerenv(self):
        with patch.dict(os.environ, {"CONTAINER_ID": "box"}), \
                patch.object(host_shell.os.path, "exists", return_value=True):
            self.assertTrue(host_shell.in_distrobox())
        with patch.dict(os.environ, {"CONTAINER_ID": "box"}), \
                patch.object(host_shell.os.path, "exists", return_value=False):
            self.assertFalse(host_shell.in_distrobox())
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(host_shell.os.path, "exists", return_value=True):
            self.assertFalse(host_shell.in_distrobox())

    def test_run_on_host_returns_stripped_stdout_or_none_on_failure(self):
        done = subprocess.CompletedProcess([], 0, stdout=" /usr/bin/zsh\n")
        with patch.object(host_shell.subprocess, "run", return_value=done) as run:
            self.assertEqual(host_shell._run_on_host(["echo"]), "/usr/bin/zsh")
            self.assertEqual(run.call_args.args[0], ["distrobox-host-exec", "echo"])
        for error in (OSError("missing"), subprocess.TimeoutExpired("x", 1)):
            with patch.object(host_shell.subprocess, "run", side_effect=error):
                self.assertIsNone(host_shell._run_on_host(["echo"]))

    def test_tmp_is_shared_requires_host_to_see_probe_file(self):
        with patch.object(host_shell, "_run_on_host", return_value="yes") as run:
            self.assertTrue(host_shell._tmp_is_shared())
            probe_path = run.call_args.args[0][-1]
            self.assertIn("zashterminal_probe_", probe_path)
        with patch.object(host_shell, "_run_on_host", return_value=""):
            self.assertFalse(host_shell._tmp_is_shared())
        with patch.object(host_shell, "_run_on_host", return_value=None):
            self.assertFalse(host_shell._tmp_is_shared())


if __name__ == "__main__":
    unittest.main()
