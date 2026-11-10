import os
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


if __name__ == "__main__":
    unittest.main()
