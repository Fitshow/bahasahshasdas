# T1 - Servidor HTTP/1.1

Servidor didático de arquivos estáticos feito em Python 3 diretamente sobre
sockets TCP. O parsing das requisições e a construção das respostas HTTP são
manuais; não há framework nem implementação pronta de servidor HTTP.

## Requisitos e execução

É necessário somente Python 3. Na raiz do projeto, execute:

```powershell
python server.py --port 8080 --root ./www
```

`--port` define a porta TCP (de 1 a 65535) e `--root` define o diretório que
pode ser publicado. O servidor faz bind em `0.0.0.0`, cria uma thread por
conexão e encerra conexões ociosas depois de aproximadamente 5 segundos.

As conexões HTTP/1.1 são persistentes por padrão. O mesmo socket aceita várias
requisições, inclusive pipelined; `Connection: close` pede o encerramento após
a resposta.

## Estrutura

```text
.
|-- server.py
|-- README.md
|-- tests/
|   `-- test_server.py
`-- www/
    |-- index.html
    |-- style.css
    |-- script.js
    `-- teste.txt
```

## Testes

Para executar a verificação automatizada (ela inicia o servidor em uma porta
livre e usa somente a biblioteca padrão):

```powershell
python -m unittest -v
```

Para os testes manuais abaixo, deixe o servidor em execução e use outro
terminal.

GET (200):

```powershell
curl.exe -v http://127.0.0.1:8080/index.html
```

HEAD (200, `Content-Length` presente e sem corpo):

```powershell
curl.exe -I http://127.0.0.1:8080/index.html
```

Arquivo inexistente (404):

```powershell
curl.exe -v http://127.0.0.1:8080/arquivo-que-nao-existe.txt
```

Método não permitido (405 e `Allow: GET, HEAD`):

```powershell
curl.exe -v -X POST http://127.0.0.1:8080/index.html
```

Directory traversal (403). `--path-as-is` impede o curl de normalizar o
caminho antes de enviá-lo:

```powershell
curl.exe -v --path-as-is http://127.0.0.1:8080/../../server.py
curl.exe -v --path-as-is http://127.0.0.1:8080/%2e%2e/%2e%2e/server.py
curl.exe -v --path-as-is http://127.0.0.1:8080/..%2f..%2fserver.py
```

Requisição malformada (400), apenas com a biblioteca padrão do Python:

```powershell
python -c "import socket; s=socket.create_connection(('127.0.0.1',8080)); s.sendall(b'GET / sem-versao\r\nHost: localhost\r\n\r\n'); print(s.recv(4096).decode('iso-8859-1')); s.close()"
```

Para testar no navegador, abra <http://127.0.0.1:8080/>. A página solicita
também `style.css` e `script.js`, produzindo requisições adicionais nos logs.

Para acessar de outra máquina na mesma rede, descubra o IPv4 do computador
servidor com `ipconfig`, permita a porta escolhida no firewall se necessário e
abra `http://IP_DO_SERVIDOR:8080/` no outro dispositivo.

## O que o servidor suporta

- GET e HEAD, com 200, 400, 403, 404 e 405;
- `Content-Length`, `Content-Type`, `Date`, `Server` e `Allow` no 405;
- arquivos HTML, CSS, JavaScript, JSON, texto, PNG, JPEG, PDF e tipo genérico;
- percent-decoding e bloqueio de directory traversal pelo caminho real;
- buffer por conexão, preservando bytes da requisição seguinte;
- conexões persistentes, `Connection: close`, timeout e concorrência por threads;
- logs de abertura, requisições, timeout e encerramento de cada conexão.

## Checklist para a entrega e a apresentação

Os testes locais validam a implementação, mas as evidências de rede exigidas
pelo enunciado devem ser coletadas entre duas máquinas distintas, nunca em
`localhost`:

- confirmar `ping` entre as duas máquinas e registrar o RTT médio;
- capturar no Wireshark uma transação GET completa;
- capturar C1: 10 requisições, cada uma com `Connection: close`;
- capturar C2: 10 requisições na mesma conexão persistente;
- salvar as capturas em `capturas/c1.pcapng` e `capturas/c2.pcapng`;
- registrar handshakes, pacotes, bytes e tempo total de C1 e C2;
- incluir no relatório a arquitetura, a tabela de conformidade, os três testes
  de travessia, simultaneidade, comparação C1/C2, overhead, relação com o RTT
  e conclusão;
- exportar o relatório final como um único arquivo PDF.
