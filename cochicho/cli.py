"""Comando `cochicho`. O atalho do GNOME chama `cochicho alternar`; o systemd chama `cochicho servidor`.

Os comandos de cliente só falam com o socket: nada pesado é importado, para o atalho responder na hora.
"""

import json
import socket
import subprocess
import sys
import time

from . import config

AJUDA = """uso: cochicho <comando>

  alternar       começa a gravar; se já está gravando, para, transcreve e cola (é o que o atalho chama)
  cancelar       descarta o ditado em curso
  estado         mostra o estado do servidor e do modelo
  ultimo         copia de novo o último texto ditado
  testar-colar   daqui a 3 s cola uma frase de teste onde estiver o cursor
                 (na primeira vez o GNOME pede permissão para o controle do teclado)
  historico [n]  mostra os últimos n ditados (padrão 10)
  servidor       roda o servidor; quem chama é o systemd (systemctl --user start cochicho)
"""

COMANDOS_DO_SERVIDOR = {"alternar", "cancelar", "estado", "ultimo", "testar-colar"}


def enviar(comando: str) -> str:
    for tentativa in range(2):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(10)
                s.connect(str(config.SOCKET))
                s.sendall((comando + "\n").encode())
                return s.makefile(encoding="utf-8").readline().strip()
        except (FileNotFoundError, ConnectionRefusedError):
            if tentativa:
                raise
            # servidor fora do ar: pede ao systemd e espera o socket aparecer
            subprocess.run(["systemctl", "--user", "start", "cochicho.service"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(50):
                if config.SOCKET.exists():
                    break
                time.sleep(0.1)
    raise ConnectionRefusedError(str(config.SOCKET))


def historico(n: int):
    try:
        with open(config.HISTORICO, encoding="utf-8") as f:
            linhas = f.read().splitlines()[-n:]
    except FileNotFoundError:
        print("nenhum ditado ainda")
        return
    for linha in linhas:
        d = json.loads(linha)
        print(f"{d['quando']}  {d['segundos']:>5.1f} s fala  {d['espera']:>4.1f} s espera  {d['texto']}")


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help", "ajuda"}:
        print(AJUDA)
        return 0
    comando = args[0]
    if comando == "servidor":
        from .servidor import servir

        servir()
        return 0
    if comando == "historico":
        historico(int(args[1]) if len(args) > 1 else 10)
        return 0
    if comando in COMANDOS_DO_SERVIDOR:
        try:
            print(enviar(comando))
            return 0
        except OSError as e:
            mensagem = f"O servidor do cochicho não respondeu ({e}). Veja: journalctl --user -u cochicho"
            print(mensagem, file=sys.stderr)
            subprocess.run(["notify-send", "-a", "Cochicho", "Cochicho: servidor fora do ar", mensagem], check=False)
            return 1
    print(AJUDA, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
