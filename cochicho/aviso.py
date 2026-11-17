"""Avisos do GNOME (org.freedesktop.Notifications) e sons curtos.

Um aviso só, atualizado no lugar (replaces_id): "Gravando" (crítico, fica na tela e tem o botão Cancelar),
"Transcrevendo", e o resultado. Falha de aviso nunca derruba o ditado.
"""

import logging
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

log = logging.getLogger(__name__)

DESTINO = "org.freedesktop.Notifications"
CAMINHO = "/org/freedesktop/Notifications"

SONS = Path("/usr/share/sounds/freedesktop/stereo")
SOM = {"inicio": "message-new-instant.oga", "pronto": "complete.oga", "erro": "dialog-warning.oga"}


class Avisos:
    def __init__(self, ao_cancelar):
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._id = 0
        self._ao_cancelar = ao_cancelar
        threading.Thread(target=self._escutar_botoes, name="avisos", daemon=True).start()

    def _escutar_botoes(self):
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        self._bus.signal_subscribe(
            None, DESTINO, "ActionInvoked", CAMINHO, None, Gio.DBusSignalFlags.NONE, self._ao_clicar
        )
        GLib.MainLoop.new(ctx, False).run()

    def _ao_clicar(self, _con, _rem, _cam, _int, _sinal, parametros):
        nid, acao = parametros.unpack()
        if nid == self._id and acao == "cancelar":
            self._ao_cancelar()

    def _notificar(self, titulo, corpo="", icone="audio-input-microphone", urgencia=1, acoes=(), transitorio=False):
        dicas = {
            "urgency": GLib.Variant("y", urgencia),
            "desktop-entry": GLib.Variant("s", "cochicho"),
            "transient": GLib.Variant("b", transitorio),
        }
        try:
            r = self._bus.call_sync(
                DESTINO, CAMINHO, DESTINO, "Notify",
                GLib.Variant("(susssasa{sv}i)", ("Cochicho", self._id, icone, titulo, corpo, list(acoes), dicas, -1)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 2000, None,
            )
            self._id = r.unpack()[0]
        except GLib.Error as e:
            log.warning("aviso falhou: %s", e.message)

    def fechar(self):
        if not self._id:
            return
        try:
            self._bus.call_sync(DESTINO, CAMINHO, DESTINO, "CloseNotification", GLib.Variant("(u)", (self._id,)),
                                None, Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass

    # --- os estados do ditado ---

    def gravando(self, carregando_modelo: bool):
        corpo = "Fale. Aperte o atalho de novo para colar."
        if carregando_modelo:
            corpo += "\n(o modelo ainda está carregando)"
        self._notificar("Gravando…", corpo, urgencia=2, acoes=("cancelar", "Cancelar"))

    def transcrevendo(self):
        self._notificar("Transcrevendo…", icone="content-loading-symbolic", transitorio=True)

    def colado(self, texto: str, espera: float, colou: bool, tecla: str):
        resumo = texto if len(texto) <= 160 else texto[:157] + "…"
        if colou:
            self._notificar(f"Colado ({espera:.1f} s)", resumo, icone="edit-paste-symbolic", transitorio=True)
        else:
            atalho = "Ctrl+V" if tecla in ("ctrl+v", "nenhum") else tecla.title()
            self._notificar(f"Copiado: cole com {atalho}", resumo, icone="edit-copy-symbolic", transitorio=True)

    def cancelado(self):
        self._notificar("Ditado cancelado", icone="process-stop-symbolic", transitorio=True)

    def vazio(self):
        self._notificar("Não ouvi nada", "Confira o microfone em Configurações › Som.",
                        icone="microphone-sensitivity-muted-symbolic", transitorio=True)

    def erro(self, mensagem: str):
        self._notificar("Cochicho: erro", mensagem, icone="dialog-error-symbolic", transitorio=True)

    def info(self, titulo: str, corpo: str = ""):
        self._notificar(titulo, corpo, transitorio=True)


def tocar(nome: str, ligado: bool):
    if not ligado:
        return
    arquivo = SONS / SOM[nome]
    if arquivo.exists():
        try:
            subprocess.Popen(["pw-play", "--volume", "0.5", str(arquivo)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
