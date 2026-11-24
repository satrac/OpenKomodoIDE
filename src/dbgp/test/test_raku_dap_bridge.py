#!/usr/bin/env python3
"""Integration test for the Raku DAP-to-DBGP bridge.

Run directly:

    python3 src/dbgp/test/test_raku_dap_bridge.py

The test is skipped when Rakudo or the nimala DAP adapter is unavailable.
"""

import base64
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from xml.dom import minidom


BRIDGE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                      "raku_dap_bridge.py")
HAS_RAKU = bool(shutil.which("raku"))
HAS_DAP = bool(shutil.which("nimala-raku-dap"))


class ExceptionFilterParsingTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.dirname(BRIDGE))
        import raku_dap_bridge
        self.parse = raku_dap_bridge.parse_exception_filters

    def test_defaults_and_values(self):
        self.assertEqual(self.parse("uncaught"), ["uncaught"])
        self.assertEqual(self.parse("all"), ["all"])
        self.assertEqual(self.parse("none"), ["none"])
        self.assertEqual(self.parse("uncaught,all"), ["uncaught", "all"])
        self.assertEqual(self.parse(""), ["uncaught"])


@unittest.skipUnless(HAS_RAKU and HAS_DAP,
                     "Rakudo and nimala-raku-dap are required")
class RakuDAPBridgeTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.program = os.path.join(self.tempdir.name, "debug.raku")
        with open(self.program, "w", encoding="utf-8") as program:
            program.write("my $x = {a => 1, b => 2};\n"
                          "say $x;\n")
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.conn = None
        self.buffer = b""
        self.proc = subprocess.Popen(
            [sys.executable, BRIDGE,
             "--host", "127.0.0.1",
             "--port", str(self.port),
             "--program", self.program,
             "--cwd", self.tempdir.name,
             "--raku", shutil.which("raku"),
             "--adapter", shutil.which("nimala-raku-dap")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True)

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        if self.conn:
            self.conn.close()
        self.listener.close()
        if self.proc.stdout:
            self.proc.stdout.close()
        if self.proc.stderr:
            self.proc.stderr.close()
        self.tempdir.cleanup()

    def _recv_packet(self):
        while b"\0" not in self.buffer:
            data = self.conn.recv(4096)
            if not data:
                return None
            self.buffer += data
        end = self.buffer.index(b"\0")
        size = int(self.buffer[:end])
        rest = self.buffer[end + 1:]
        while len(rest) < size:
            data = self.conn.recv(4096)
            if not data:
                return None
            rest += data
        payload = rest[:size]
        self.buffer = rest[size + 1:]
        return payload.decode("utf-8", "replace")

    def _send(self, command):
        self.conn.sendall(command.encode("utf-8") + b"\0")

    def _recv_xml(self, command):
        while True:
            payload = self._recv_packet()
            self.assertIsNotNone(payload, "bridge closed the DBGP connection")
            try:
                document = minidom.parseString(payload)
            except Exception:
                continue
            root = document.documentElement
            if root.localName == "init":
                if command == "init":
                    return root
                continue
            if root.getAttribute("command") == command:
                return root

    def test_debug_session(self):
        self.conn, _ = self.listener.accept()
        self.conn.settimeout(30)
        init = self._recv_xml("init")
        self.assertEqual(init.getAttribute("language"), "Raku")
        self.assertTrue(init.getAttribute("fileuri").endswith("debug.raku"))

        for index, name in enumerate(
                ["language_name", "supports_async", "max_children",
                 "max_data", "max_depth", "show_hidden"], 1):
            self._send("feature_get -n %s -i %d" % (name, index))
            self._recv_xml("feature_get")

        self._send(
            "breakpoint_set -i 20 -t line -f %s -n 2"
            % ("file://" + self.program))
        self.assertTrue(self._recv_xml("breakpoint_set").getAttribute("id"))

        self._send("run -i 30")
        run = self._recv_xml("run")
        self.assertEqual(run.getAttribute("status"), "break")

        self._send("stack_get -i 40")
        stack = self._recv_xml("stack_get")
        frames = stack.getElementsByTagName("stack")
        self.assertTrue(frames)
        self.assertEqual(int(frames[0].getAttribute("lineno")), 2)

        self._send("context_names -i 41")
        contexts = self._recv_xml("context_names")
        self.assertTrue(contexts.getElementsByTagName("context"))

        self._send("context_get -c 0 -d 0 -i 42")
        context = self._recv_xml("context_get")
        properties = context.getElementsByTagName("property")
        self.assertTrue(properties)
        x_prop = [prop for prop in properties
                  if prop.getAttribute("name") == "$x"]
        self.assertTrue(x_prop)
        self.assertEqual(x_prop[0].getAttribute("children"), "1")

        self._send("property_get -i 43 -c 0 -d 0 -n $x")
        prop = self._recv_xml("property_get")
        self.assertTrue(prop.getElementsByTagName("property"))

        expression = "$x<a>"
        self._send("eval -i 44 -l %d -- %s" % (
            len(expression),
            base64.b64encode(expression.encode("utf-8")).decode("ascii")))
        evaluate = self._recv_xml("eval")
        value = evaluate.getElementsByTagName("property")
        self.assertTrue(value)
        self.assertEqual(
            base64.b64decode(value[0].firstChild.data).decode("utf-8"), "1")

        self._send("restart -i 45")
        restart = self._recv_xml("restart")
        self.assertEqual(restart.getAttribute("status"), "running")

        status = None
        deadline = time.time() + 20
        request_id = 46
        while time.time() < deadline:
            self._send("status -i %d" % request_id)
            request_id += 1
            status = self._recv_xml("status").getAttribute("status")
            if status == "break":
                break
            time.sleep(0.25)
        self.assertEqual(status, "break")

        self._send("stop -i 50")
        self._recv_xml("stop")


if __name__ == "__main__":
    unittest.main(verbosity=2)
