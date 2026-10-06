import argparse
import mimetypes
import os
import socket
import threading
from email.utils import formatdate
from pathlib import Path
from urllib.parse import unquote


SERVER_NAME = "Grupo-7"
BUFFER_SIZE = 4096
MAX_HEADER_SIZE = 64 * 1024
MAX_BODY_SIZE = 10 * 1024 * 1024
CLIENT_TIMEOUT = 5

STATUS_TEXT = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
}

TOKEN_CHARACTERS = set(
    "!#$%&'*+-.^_`|~0123456789"
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)

print_lock = threading.Lock()


class BadRequest(ValueError):
    pass


class ForbiddenPath(ValueError):
    pass


def log(message):
    with print_lock:
        print(message, flush=True)


def valid_port(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("a porta deve estar entre 1 e 65535")
    return port


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Servidor HTTP/1.1 sobre sockets TCP"
    )
    parser.add_argument("--port", type=valid_port, required=True, help="porta TCP")
    parser.add_argument(
        "--root", required=True, help="diretório que contém os arquivos publicados"
    )
    return parser.parse_args()


def parse_request(header_bytes):
    try:
        text = header_bytes.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise BadRequest("headers não puderam ser decodificados") from exc

    lines = text.split("\r\n")
    if not lines or not lines[0]:
        raise BadRequest("request line ausente")

    request_parts = lines[0].split(" ")
    if len(request_parts) != 3 or not all(request_parts):
        raise BadRequest("request line deve ter exatamente três partes")

    method, target, version = request_parts
    if not method or any(character not in TOKEN_CHARACTERS for character in method):
        raise BadRequest("método inválido")
    if not target or not target.startswith("/"):
        raise BadRequest("request-target inválido")
    if version != "HTTP/1.1":
        raise BadRequest("somente HTTP/1.1 é suportado")

    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        if line[0] in " \t" or ":" not in line:
            raise BadRequest("header malformado")

        name, value = line.split(":", 1)
        if not name or any(character not in TOKEN_CHARACTERS for character in name):
            raise BadRequest("nome de header inválido")

        value = value.strip(" \t")
        if any((ord(character) < 32 and character != "\t") or ord(character) == 127
               for character in value):
            raise BadRequest("valor de header inválido")

        lower_name = name.lower()
        if lower_name == "content-length" and lower_name in headers:
            if headers[lower_name] != value:
                raise BadRequest("Content-Length conflitante")
        elif lower_name in headers:
            headers[lower_name] += ", " + value
        else:
            headers[lower_name] = value

    if "host" not in headers or not headers["host"]:
        raise BadRequest("Host é obrigatório em HTTP/1.1")

    if "transfer-encoding" in headers:
        raise BadRequest("Transfer-Encoding não é suportado")

    content_length_text = headers.get("content-length", "0")
    if not content_length_text.isdecimal():
        raise BadRequest("Content-Length inválido")
    content_length = int(content_length_text)
    if content_length > MAX_BODY_SIZE:
        raise BadRequest("corpo da requisição muito grande")

    return method, target, version, headers, content_length


def resolve_requested_path(root, target):
    url_path = target.split("?", 1)[0]
    try:
        decoded_path = unquote(url_path, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BadRequest("caminho possui percent-encoding inválido") from exc

    if "\x00" in decoded_path:
        raise BadRequest("caminho contém byte nulo")
    if decoded_path == "/":
        decoded_path = "/index.html"

    relative_path = decoded_path.lstrip("/\\")
    candidate = os.path.realpath(os.path.join(root, relative_path))
    try:
        inside_root = os.path.commonpath([root, candidate]) == root
    except ValueError:
        inside_root = False

    if not inside_root:
        raise ForbiddenPath("tentativa de acesso fora do root")
    return candidate


def get_content_type(path):
    extension = Path(path).suffix.lower()
    if extension in CONTENT_TYPES:
        return CONTENT_TYPES[extension]
    guessed_type, _ = mimetypes.guess_type(path)
    return guessed_type or "application/octet-stream"


def error_body(status_code):
    reason = STATUS_TEXT[status_code]
    return (
        "<!doctype html>\n"
        '<html lang="pt-BR"><meta charset="utf-8">'
        f"<title>{status_code} {reason}</title>"
        f"<h1>{status_code} {reason}</h1></html>\n"
    ).encode("utf-8")


def build_response(status_code, body, content_type, close_connection=False,
                   extra_headers=None, include_body=True):
    reason = STATUS_TEXT[status_code]
    headers = [
        f"HTTP/1.1 {status_code} {reason}",
        f"Date: {formatdate(usegmt=True)}",
        f"Server: {SERVER_NAME}",
        f"Content-Length: {len(body)}",
        f"Content-Type: {content_type}",
        f"Connection: {'close' if close_connection else 'keep-alive'}",
    ]
    if extra_headers:
        headers.extend(f"{name}: {value}" for name, value in extra_headers.items())

    head = ("\r\n".join(headers) + "\r\n\r\n").encode("iso-8859-1")
    return head + (body if include_body else b"")


def response_for_request(method, target, root, close_connection):
    include_body = method != "HEAD"

    if method not in ("GET", "HEAD"):
        status_code = 405
        body = error_body(status_code)
        response = build_response(
            status_code,
            body,
            "text/html; charset=utf-8",
            close_connection,
            {"Allow": "GET, HEAD"},
            include_body=True,
        )
        return response, status_code

    try:
        requested_path = resolve_requested_path(root, target)
    except ForbiddenPath:
        status_code = 403
        body = error_body(status_code)
        return build_response(
            status_code, body, "text/html; charset=utf-8",
            close_connection, include_body=include_body
        ), status_code
    except BadRequest:
        status_code = 400
        body = error_body(status_code)
        return build_response(
            status_code, body, "text/html; charset=utf-8",
            close_connection, include_body=include_body
        ), status_code

    if not os.path.isfile(requested_path):
        status_code = 404
        body = error_body(status_code)
        return build_response(
            status_code, body, "text/html; charset=utf-8",
            close_connection, include_body=include_body
        ), status_code

    try:
        with open(requested_path, "rb") as requested_file:
            body = requested_file.read()
    except PermissionError:
        status_code = 403
        body = error_body(status_code)
        return build_response(
            status_code, body, "text/html; charset=utf-8",
            close_connection, include_body=include_body
        ), status_code
    except OSError:
        status_code = 404
        body = error_body(status_code)
        return build_response(
            status_code, body, "text/html; charset=utf-8",
            close_connection, include_body=include_body
        ), status_code

    status_code = 200
    return build_response(
        status_code,
        body,
        get_content_type(requested_path),
        close_connection,
        include_body=include_body,
    ), status_code


def send_bad_request(client_socket):
    body = error_body(400)
    response = build_response(
        400, body, "text/html; charset=utf-8", close_connection=True
    )
    client_socket.sendall(response)


def receive_more(client_socket, buffer):
    data = client_socket.recv(BUFFER_SIZE)
    if not data:
        return None
    return buffer + data


def handle_client(client_socket, client_address, root):
    client_id = f"{client_address[0]}:{client_address[1]}"
    log(f"[{client_id}] conexão aberta")
    buffer = b""

    try:
        client_socket.settimeout(CLIENT_TIMEOUT)
        with client_socket:
            while True:
                header_end = buffer.find(b"\r\n\r\n")
                while header_end == -1:
                    if len(buffer) > MAX_HEADER_SIZE:
                        send_bad_request(client_socket)
                        log(f"[{client_id}] requisição inválida -> 400 Bad Request")
                        return
                    new_buffer = receive_more(client_socket, buffer)
                    if new_buffer is None:
                        return
                    buffer = new_buffer
                    header_end = buffer.find(b"\r\n\r\n")

                if header_end > MAX_HEADER_SIZE:
                    send_bad_request(client_socket)
                    log(f"[{client_id}] requisição inválida -> 400 Bad Request")
                    return

                header_bytes = buffer[:header_end]
                buffer = buffer[header_end + 4:]

                try:
                    method, target, _version, headers, content_length = parse_request(
                        header_bytes
                    )
                except BadRequest:
                    send_bad_request(client_socket)
                    log(f"[{client_id}] requisição inválida -> 400 Bad Request")
                    return

                while len(buffer) < content_length:
                    new_buffer = receive_more(client_socket, buffer)
                    if new_buffer is None:
                        return
                    buffer = new_buffer
                buffer = buffer[content_length:]

                connection_tokens = {
                    token.strip().lower()
                    for token in headers.get("connection", "").split(",")
                }
                close_connection = "close" in connection_tokens

                response, status_code = response_for_request(
                    method, target, root, close_connection
                )
                client_socket.sendall(response)
                log(
                    f"[{client_id}] {method} {target} -> "
                    f"{status_code} {STATUS_TEXT[status_code]}"
                )

                if close_connection:
                    return
    except socket.timeout:
        log(f"[{client_id}] timeout de inatividade")
    except (BrokenPipeError, ConnectionResetError):
        log(f"[{client_id}] cliente desconectou inesperadamente")
    except OSError as exc:
        log(f"[{client_id}] erro de conexão: {exc}")
    finally:
        log(f"[{client_id}] conexão encerrada")


def main():
    args = parse_arguments()
    root = os.path.realpath(args.root)
    if not os.path.isdir(root):
        raise SystemExit(f"Erro: o diretório root não existe: {root}")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_socket.bind(("0.0.0.0", args.port))
        server_socket.listen()
        log("Servidor HTTP/1.1 iniciado")
        log(f"Endereço: 0.0.0.0:{args.port}")
        log(f"Root: {root}")

        while True:
            try:
                client_socket, client_address = server_socket.accept()
            except OSError:
                break

            thread = threading.Thread(
                target=handle_client,
                args=(client_socket, client_address, root),
                daemon=True,
            )
            thread.start()
    except KeyboardInterrupt:
        log("\nServidor encerrado pelo usuário")
    finally:
        server_socket.close()


if __name__ == "__main__":
    main()
