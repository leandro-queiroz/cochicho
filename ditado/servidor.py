"""Servidor do ditado: mantém o modelo na memória e atende o atalho por um socket Unix.

Estados: parado -> (alternar) gravando -> (alternar) transcrevendo -> parado.
"""

import json
import logging
import os
import queue
import re
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime

from . import config
from .aviso import Avisos, tocar
from .gravador import Gravador
from .transcritor import Transcritor

log = logging.getLogger("ditado")

TEXTO_DE_TESTE = "Teste do ditado: olá, ação, coração."


class Sessao:
    """Um ditado: os trechos entram na fila enquanto se fala e são transcritos em ordem."""

    def __init__(self, transcritor: Transcritor, cfg: config.Config):
        self._transcritor = transcritor
        self._cfg = cfg
        self._fila: queue.Queue = queue.Queue()
        self.textos: list[str] = []
        self.cancelada = False
        self._thread = threading.Thread(target=self._trabalhar, name="sessao", daemon=True)
        self._thread.start()

    def entregar(self, audio):
        self._fila.put(audio)

    def _trabalhar(self):
        while (audio := self._fila.get()) is not None:
            if self.cancelada:
                continue
            try:
                texto = self._transcritor.transcrever(audio, self._cfg.contexto, " ".join(self.textos), self._cfg.beam)
            except Exception:  # noqa: BLE001
                log.exception("falha ao transcrever um trecho")
                continue
            if texto:
                self.textos.append(texto)

    def terminar(self) -> str:
        self._fila.put(None)
        self._thread.join()
        return " ".join(self.textos)

    def cancelar(self):
        self.cancelada = True
        self._fila.put(None)


class Ditado:
    def __init__(self):
        self._trava = threading.RLock()
        self.estado = "parado"
        self._gravador: Gravador | None = None
        self._sessao: Sessao | None = None
        self.ultimo = ""
        self.avisos = Avisos(ao_cancelar=self.cancelar)
        self.cfg = config.Config()
        self._ler_config()
        self.transcritor = Transcritor()
        self.transcritor.carregar_em_segundo_plano(self.cfg.modelo, self.cfg.threads)

    def _ler_config(self):
        try:
            self.cfg = config.carregar()
        except config.ConfigInvalida as e:
            log.error("%s (mantida a configuração anterior)", e)
            self.avisos.erro(f"{e}\nMantida a configuração anterior.")

    # --- comandos ---

    def executar(self, comando: str) -> str:
        acoes = {
            "alternar": self.alternar,
            "cancelar": self.cancelar,
            "estado": self.descrever,
            "ultimo": self.copiar_ultimo,
            "testar-colar": self.testar_colar,
        }
        acao = acoes.get(comando)
        return acao() if acao else f"comando desconhecido: {comando}"

    def alternar(self) -> str:
        with self._trava:
            if self.estado == "parado":
                return self._iniciar()
            if self.estado == "gravando":
                self.estado = "transcrevendo"
                threading.Thread(target=self._finalizar, args=(self._gravador, self._sessao), daemon=True).start()
                return "transcrevendo"
            return "ocupado: ainda transcrevendo"

    def cancelar(self) -> str:
        with self._trava:
            if self.estado == "gravando":
                self._gravador.cancelar()
                self._sessao.cancelar()
                self.estado = "parado"
            elif self.estado == "transcrevendo":
                self._sessao.cancelada = True  # o _finalizar vê e não cola
            else:
                return "nada para cancelar"
        self.avisos.cancelado()
        return "cancelado"

    def descrever(self) -> str:
        nome, threads = self.transcritor.chave
        situacao = "carregando" if self.transcritor.carregando else "pronto"
        return f"{self.estado}; modelo {nome} ({threads} threads) {situacao}; colar com {self.cfg.colar_com}"

    def copiar_ultimo(self) -> str:
        texto = self.ultimo or _ultimo_do_historico()
        if not texto:
            return "nenhum ditado ainda"
        _copiar(texto)
        return f"copiado: {texto[:80]}"

    def testar_colar(self) -> str:
        def testar():
            time.sleep(3)
            colou = self._colar(TEXTO_DE_TESTE)
            self.avisos.colado(TEXTO_DE_TESTE, 0.0, colou, self.cfg.colar_com)

        threading.Thread(target=testar, daemon=True).start()
        return "em 3 s cola a frase de teste onde estiver o cursor"

    # --- ciclo do ditado ---

    def _iniciar(self) -> str:
        self._ler_config()
        cfg = self.cfg
        if (cfg.modelo, cfg.threads) != self.transcritor.chave:
            self.transcritor.carregar_em_segundo_plano(cfg.modelo, cfg.threads)
        sessao = Sessao(self.transcritor, cfg)
        gravador = Gravador(
            ao_trecho=sessao.entregar,
            ao_limite=lambda: self._parar_se(gravador),
            ao_falhar=lambda msg: self._falhou(gravador, msg),
            max_segundos=cfg.max_segundos,
        )
        try:
            gravador.iniciar()
        except OSError as e:
            sessao.cancelar()
            self.avisos.erro(f"Não consegui abrir o microfone: {e}")
            return "erro"
        self._gravador, self._sessao = gravador, sessao
        self.estado = "gravando"
        tocar("inicio", cfg.sons)
        self.avisos.gravando(self.transcritor.carregando)
        return "gravando"

    def _parar_se(self, gravador: Gravador):
        with self._trava:
            if self._gravador is gravador and self.estado == "gravando":
                log.info("limite de %d s atingido", self.cfg.max_segundos)
                self.alternar()

    def _falhou(self, gravador: Gravador, mensagem: str):
        with self._trava:
            if self._gravador is not gravador or self.estado != "gravando":
                return
            self._sessao.cancelar()
            self.estado = "parado"
        tocar("erro", self.cfg.sons)
        self.avisos.erro(mensagem)

    def _finalizar(self, gravador: Gravador, sessao: Sessao):
        t0 = time.monotonic()
        try:
            duracao = gravador.parar()
            self.avisos.transcrevendo()
            texto = self._pos_processar(sessao.terminar())
            if sessao.cancelada:
                return
            if not texto:
                tocar("erro", self.cfg.sons)
                self.avisos.vazio()
                return
            self.ultimo = texto
            colou = self._colar(texto)
            espera = time.monotonic() - t0
            log.info("ditado de %.1f s: %d caracteres, %.1f s de espera, colou=%s", duracao, len(texto), espera, colou)
            self._registrar(texto, duracao, espera)
            tocar("pronto", self.cfg.sons)
            self.avisos.colado(texto, espera, colou, self.cfg.colar_com)
        except Exception as e:  # noqa: BLE001
            log.exception("falha ao finalizar o ditado")
            self.avisos.erro(f"{type(e).__name__}: {e}")
        finally:
            with self._trava:
                if self._sessao is sessao:
                    self.estado = "parado"
                    self._gravador = self._sessao = None

    def _pos_processar(self, texto: str) -> str:
        texto = re.sub(r"\s+", " ", texto).strip()
        for de, para in self.cfg.substituicoes.items():
            texto = re.sub(rf"(?<!\w){re.escape(de)}(?!\w)", lambda _m, p=para: p, texto, flags=re.IGNORECASE)
        return texto

    def _colar(self, texto: str) -> bool:
        """Copia o texto e aperta a tecla de colar na janela em foco. False: ficou só copiado."""
        _copiar(texto)
        if self.cfg.colar_com == "nenhum":
            return False
        time.sleep(0.1)  # o GNOME precisa ver a área de transferência nova antes do Ctrl+V
        r = subprocess.run(
            [sys.executable, "-m", "ditado.portal", self.cfg.colar_com, str(config.TOKEN_PORTAL)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            log.warning("colar pelo portal falhou (código %d): %s", r.returncode, r.stderr.strip())
            return False
        return True

    def _registrar(self, texto: str, duracao: float, espera: float):
        if not self.cfg.historico:
            return
        config.DADOS_DIR.mkdir(parents=True, exist_ok=True)
        novo = not config.HISTORICO.exists()
        with open(config.HISTORICO, "a", encoding="utf-8") as f:
            linha = {
                "quando": datetime.now().isoformat(timespec="seconds"),
                "segundos": round(duracao, 1),
                "espera": round(espera, 1),
                "modelo": self.cfg.modelo,
                "texto": texto,
            }
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")
        if novo:
            config.HISTORICO.chmod(0o600)


def _copiar(texto: str):
    # stdout/stderr no DEVNULL: o wl-copy deixa um filho servindo a área de transferência, que seguraria um pipe.
    subprocess.run(["wl-copy"], input=texto.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=5, check=True)


def _ultimo_do_historico() -> str:
    try:
        with open(config.HISTORICO, encoding="utf-8") as f:
            linhas = f.read().splitlines()
        return json.loads(linhas[-1])["texto"] if linhas else ""
    except (OSError, ValueError, KeyError):
        return ""


class _Atendente(socketserver.StreamRequestHandler):
    def handle(self):
        comando = self.rfile.readline(200).decode(errors="replace").strip()
        try:
            resposta = self.server.ditado.executar(comando)
        except Exception as e:  # noqa: BLE001
            log.exception("falha no comando %s", comando)
            resposta = f"erro: {e}"
        self.wfile.write((resposta.replace("\n", " ") + "\n").encode())


def servir():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    caminho = config.SOCKET
    if caminho.exists():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(str(caminho))
            log.error("já existe um servidor do ditado rodando em %s", caminho)
            sys.exit(1)
        except (ConnectionRefusedError, FileNotFoundError):
            caminho.unlink(missing_ok=True)

    def sair(*_):
        caminho.unlink(missing_ok=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, sair)
    signal.signal(signal.SIGINT, sair)
    ditado = Ditado()
    with socketserver.ThreadingUnixStreamServer(str(caminho), _Atendente) as servidor:
        caminho.chmod(0o600)
        servidor.daemon_threads = True
        servidor.ditado = ditado
        log.info("ouvindo em %s", caminho)
        servidor.serve_forever()
