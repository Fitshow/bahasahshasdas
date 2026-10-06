import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER = PROJECT_ROOT / "server.py"
WWW_ROOT = PROJECT_ROOT / "www"


def read_response(connection, expect_body=True):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(4096)
        if not chunk:
            break
        data += chunk

    header_bytes, separator, body = data.partition(b"\r\n\r\n")
    if not separator:
        raise AssertionError(f"resposta sem fim de cabeçalho: {data!r}")

    lines = header_bytes.decode("iso-8859-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, value = line.split(":", 1)
        headers[name.lower()] = value.strip()

    content_length = int(headers["content-length"])
    if expect_body:
        while len(body) < content_length:
            body += connection.recv(4096)
        if len(body) != content_length:
            raise AssertionError("corpo recebido tem tamanho incorreto")
    elif body:
        raise AssertionError("resposta HEAD contém corpo")

    return lines[0], headers, body


class ServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as temporary_socket:
            temporary_socket.bind(("127.0.0.1", 0))
            cls.port = temporary_socket.getsockname()[1]

        cls.process = subprocess.Popen(
            [
                sys.executable,
                str(SERVER),
                "--port",
                str(cls.port),
                "--root",
                str(WWW_ROOT),
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                error = cls.process.stderr.read()
                raise RuntimeError(f"servidor encerrou durante a inicialização: {error}")
            try:
                with socket.create_connection(
                    ("127.0.0.1", cls.port), timeout=0.2
                ):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("servidor não iniciou dentro do tempo esperado")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait(timeout=3)

    def connect(self):
        return socket.create_connection(("127.0.0.1", self.port), timeout=2)

    @staticmethod
    def request(method, path, connection=b"close"):
        return (
            method
            + b" "
            + path
            + b" HTTP/1.1\r\nHost: localhost\r\nConnection: "
            + connection
            + b"\r\n\r\n"
        )

    def exchange(self, request_bytes, expect_body=True):
        with self.connect() as connection:
            connection.sendall(request_bytes)
            return read_response(connection, expect_body)

    def test_get_and_head(self):
        status, headers, body = self.exchange(self.request(b"GET", b"/index.html"))
        self.assertEqual(status, "HTTP/1.1 200 OK")
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(int(headers["content-length"]), len(body))
        self.assertIn(b"style.css", body)
        self.assertIn(b"script.js", body)
        self.assertIn(b"apresentacao.gif", body)

        status, headers, body = self.exchange(
            self.request(b"GET", b"/apresentacao.gif")
        )
        self.assertEqual(status, "HTTP/1.1 200 OK")
        self.assertEqual(headers["content-type"], "image/gif")
        self.assertTrue(body.startswith(b"GIF"))

        status, headers, body = self.exchange(
            self.request(b"HEAD", b"/index.html"), expect_body=False
        )
        self.assertEqual(status, "HTTP/1.1 200 OK")
        self.assertGreater(int(headers["content-length"]), 0)
        self.assertEqual(body, b"")

    def test_required_error_statuses(self):
        status, _, _ = self.exchange(self.request(b"GET", b"/nao-existe"))
        self.assertEqual(status, "HTTP/1.1 404 Not Found")

        status, headers, _ = self.exchange(self.request(b"POST", b"/index.html"))
        self.assertEqual(status, "HTTP/1.1 405 Method Not Allowed")
        self.assertEqual(headers["allow"], "GET, HEAD")

        malformed = b"GET / sem-versao\r\nHost: localhost\r\n\r\n"
        status, _, _ = self.exchange(malformed)
        self.assertEqual(status, "HTTP/1.1 400 Bad Request")

    def test_three_directory_traversal_variants(self):
        paths = (
            b"/../../Windows/System32/drivers/etc/hosts",
            b"/%2e%2e/%2e%2e/Windows/System32/drivers/etc/hosts",
            b"/..%2f..%2fWindows/System32/drivers/etc/hosts",
        )
        for path in paths:
            with self.subTest(path=path):
                status, _, _ = self.exchange(self.request(b"GET", path))
                self.assertEqual(status, "HTTP/1.1 403 Forbidden")

    def test_persistent_connection_and_close(self):
        with self.connect() as connection:
            connection.sendall(self.request(b"GET", b"/teste.txt", b"keep-alive"))
            first_status, first_headers, _ = read_response(connection)
            self.assertEqual(first_status, "HTTP/1.1 200 OK")
            self.assertEqual(first_headers["connection"], "keep-alive")

            connection.sendall(self.request(b"GET", b"/index.html"))
            second_status, second_headers, _ = read_response(connection)
            self.assertEqual(second_status, "HTTP/1.1 200 OK")
            self.assertEqual(second_headers["connection"], "close")
            self.assertEqual(connection.recv(1), b"")

    def test_slow_client_does_not_block_another_client(self):
        slow_connection = self.connect()
        self.addCleanup(slow_connection.close)
        slow_connection.sendall(b"GET /index.html HTTP/1.1\r\n")
        result = []

        def fast_client():
            result.append(self.exchange(self.request(b"GET", b"/index.html"))[0])

        thread = threading.Thread(target=fast_client)
        thread.start()
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["HTTP/1.1 200 OK"])


if __name__ == "__main__":
    unittest.main()
