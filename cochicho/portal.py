"""Aperta uma combinação de teclas pelo portal RemoteDesktop (xdg-desktop-portal), o único jeito de um app comum
mandar tecla para outra janela no GNOME com Wayland.

Roda como processo separado, chamado pelo servidor:

    python -m cochicho.portal ctrl+shift+v ~/.local/share/cochicho/portal-token

Assim um portal travado não prende o servidor. Na primeira vez o GNOME pergunta se permite o controle; a resposta
fica guardada num restore_token, que só vale uma vez e é trocado a cada uso (persist_mode 2, até revogar).

Saída: 0 ok; 2 o usuário negou; 3 sem resposta a tempo; 4 erro.
"""

import os
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

# Códigos evdev (linux/input-event-codes.h).
TECLAS = {"ctrl": 29, "shift": 42, "alt": 56, "super": 125, "v": 47, "insert": 110}

DESTINO = "org.freedesktop.portal.Desktop"
CAMINHO = "/org/freedesktop/portal/desktop"
RD = "org.freedesktop.portal.RemoteDesktop"


class SemResposta(Exception):
    pass


class Negado(Exception):
    pass


class Portal:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.remetente = self.bus.get_unique_name()[1:].replace(".", "_")
        self.contador = 0

    def pedir(self, metodo: str, montar, espera_s: int) -> dict:
        """Chama um método que responde pelo sinal Request.Response e espera a resposta."""
        self.contador += 1
        handle = f"cochicho{os.getpid()}_{self.contador}"
        caminho = f"/org/freedesktop/portal/desktop/request/{self.remetente}/{handle}"
        loop = GLib.MainLoop()
        resposta = []
        estourou = []

        def ao_responder(_con, _rem, _cam, _int, _sinal, parametros):
            resposta.append(parametros.unpack())
            loop.quit()

        def ao_estourar():
            estourou.append(True)
            loop.quit()
            return False

        sub = self.bus.signal_subscribe(
            None, "org.freedesktop.portal.Request", "Response", caminho, None, Gio.DBusSignalFlags.NONE, ao_responder
        )
        try:
            opcoes = {"handle_token": GLib.Variant("s", handle)}
            self.bus.call_sync(DESTINO, CAMINHO, RD, metodo, montar(opcoes), None, Gio.DBusCallFlags.NONE, -1, None)
            relogio = GLib.timeout_add_seconds(espera_s, ao_estourar)
            loop.run()
            if not estourou:
                GLib.source_remove(relogio)
        finally:
            self.bus.signal_unsubscribe(sub)
        if not resposta:
            raise SemResposta(metodo)
        codigo, resultados = resposta[0]
        if codigo != 0:
            raise Negado(metodo)
        return resultados

    def tecla(self, sessao: str, codigo: int, apertada: bool):
        self.bus.call_sync(
            DESTINO, CAMINHO, RD, "NotifyKeyboardKeycode",
            GLib.Variant("(oa{sv}iu)", (sessao, {}, codigo, 1 if apertada else 0)),
            None, Gio.DBusCallFlags.NONE, -1, None,
        )
        time.sleep(0.015)

    def fechar(self, sessao: str):
        try:
            self.bus.call_sync(DESTINO, sessao, "org.freedesktop.portal.Session", "Close", None, None,
                               Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error:
            pass


def apertar(combinacao: str, arquivo_token: Path) -> None:
    codigos = [TECLAS[t.strip()] for t in combinacao.lower().split("+")]
    token = arquivo_token.read_text().strip() if arquivo_token.exists() else ""
    p = Portal()

    def criar(o):
        o["session_handle_token"] = GLib.Variant("s", f"cochicho{os.getpid()}")
        return GLib.Variant("(a{sv})", (o,))

    sessao = p.pedir("CreateSession", criar, 15)["session_handle"]
    try:
        def selecionar(o):
            o["types"] = GLib.Variant("u", 1)  # teclado
            o["persist_mode"] = GLib.Variant("u", 2)  # até o usuário revogar
            if token:
                o["restore_token"] = GLib.Variant("s", token)
            return GLib.Variant("(oa{sv})", (sessao, o))

        p.pedir("SelectDevices", selecionar, 15)
        # Na primeira vez o GNOME abre a janela de permissão: espera a pessoa responder.
        resultado = p.pedir("Start", lambda o: GLib.Variant("(osa{sv})", (sessao, "", o)), 90)
        novo = resultado.get("restore_token")
        if novo and novo != token:
            arquivo_token.parent.mkdir(parents=True, exist_ok=True)
            arquivo_token.write_text(novo)
            arquivo_token.chmod(0o600)
        time.sleep(0.05)  # o teclado virtual nasce de forma assíncrona; sem a folga a primeira tecla pode se perder
        for c in codigos:
            p.tecla(sessao, c, True)
        for c in reversed(codigos):
            p.tecla(sessao, c, False)
    finally:
        p.fechar(sessao)


def main() -> int:
    if len(sys.argv) != 3:
        print("uso: python -m cochicho.portal <ctrl+shift+v> <arquivo-do-token>", file=sys.stderr)
        return 4
    try:
        apertar(sys.argv[1], Path(sys.argv[2]))
        return 0
    except Negado as e:
        print(f"negado em {e}", file=sys.stderr)
        return 2
    except SemResposta as e:
        print(f"sem resposta em {e}", file=sys.stderr)
        return 3
    except (GLib.Error, KeyError, OSError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
