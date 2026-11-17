"""Grava o microfone padrão pelo PipeWire e entrega o áudio em trechos, cortados nas pausas da fala.

O Whisper trabalha em janelas de 30 s e cobra quase o mesmo por uma janela cheia ou quase vazia. Para o ditado longo
não esperar uma janela por vez no fim, cada trecho já fechado é transcrito enquanto a pessoa ainda fala: um trecho
fecha na primeira pausa depois de TRECHO_MIN segundos, ou à força perto de TRECHO_MAX.
"""

import logging
import subprocess
import threading
import time

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps

log = logging.getLogger(__name__)

TAXA = 16000
TRECHO_MIN = 8.0  # s: antes disso não vale abrir mais uma janela do Whisper
TRECHO_MAX = 28.0  # s: abaixo dos 30 s da janela
PAUSA_MIN = 0.5  # s de silêncio entre falas para cortar ali
CHECAR_A_CADA = 1.0  # s
BLOCO = 3200  # bytes por leitura = 100 ms de s16 mono a 16 kHz

VAD = VadOptions(min_silence_duration_ms=int(PAUSA_MIN * 1000), speech_pad_ms=100)


class Gravador:
    def __init__(self, ao_trecho, ao_limite, ao_falhar, max_segundos: int):
        self._ao_trecho = ao_trecho  # recebe np.float32 em 16 kHz
        self._ao_limite = ao_limite
        self._ao_falhar = ao_falhar
        self._max = max_segundos
        self._buf = bytearray()
        self._despachado = 0  # amostras já entregues
        self._parando = False
        self._descartar = False
        self._proc = None
        self._thread = None

    @property
    def segundos(self) -> float:
        return len(self._buf) / 2 / TAXA

    def iniciar(self):
        self._proc = subprocess.Popen(
            ["pw-record", "--rate", str(TAXA), "--channels", "1", "--format", "s16", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._thread = threading.Thread(target=self._ler, name="gravador", daemon=True)
        self._thread.start()

    def parar(self) -> float:
        """Para a gravação, entrega o último trecho e devolve a duração total em segundos."""
        self._parando = True
        self._encerrar_processo()
        self._thread.join(timeout=10)
        return self.segundos

    def cancelar(self):
        self._descartar = True
        self._parando = True
        self._encerrar_processo()

    def _encerrar_processo(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _ler(self):
        ultima_checagem = time.monotonic()
        avisou_limite = False
        while True:
            dados = self._proc.stdout.read(BLOCO)
            if not dados:
                break
            self._buf += dados
            if self._descartar:
                continue
            agora = time.monotonic()
            if agora - ultima_checagem >= CHECAR_A_CADA:
                ultima_checagem = agora
                self._tentar_cortar()
            if not avisou_limite and self.segundos >= self._max:
                avisou_limite = True
                threading.Thread(target=self._ao_limite, daemon=True).start()
        if self._descartar:
            return
        if not self._parando:
            log.error("pw-record terminou sozinho (código %s)", self._proc.poll())
            self._ao_falhar("O microfone parou de responder (pw-record terminou).")
            return
        self._entregar(len(self._buf) // 2)

    def _pendente(self) -> np.ndarray:
        return np.frombuffer(self._buf, dtype=np.int16, offset=self._despachado * 2).astype(np.float32) / 32768.0

    def _tentar_cortar(self):
        audio = self._pendente()
        if len(audio) < TRECHO_MIN * TAXA:
            return
        falas = get_speech_timestamps(audio, VAD, sampling_rate=TAXA)
        limite = int(TRECHO_MAX * TAXA)
        corte = None
        if not falas:
            # só silêncio até aqui: descarta, mantendo meio segundo para não comer o começo de uma palavra
            corte = len(audio) - TAXA // 2
        else:
            # pausas entre falas, mais a pausa em curso no fim (a pessoa parou de falar agora)
            candidatos = [
                (a["end"] + b["start"]) // 2
                for a, b in zip(falas, falas[1:])
                if b["start"] - a["end"] >= PAUSA_MIN * TAXA
            ]
            if len(audio) - falas[-1]["end"] >= PAUSA_MIN * TAXA:
                candidatos.append(falas[-1]["end"] + int(0.2 * TAXA))
            candidatos = [c for c in candidatos if TRECHO_MIN * TAXA <= c <= limite]
            if candidatos:
                corte = max(candidatos)
            elif len(audio) >= limite:
                corte = self._menor_energia(audio, int(TRECHO_MIN * TAXA), limite)
        if corte:
            self._entregar(self._despachado + corte, tem_fala=bool(falas))

    @staticmethod
    def _menor_energia(audio: np.ndarray, inicio: int, fim: int) -> int:
        """Fala sem pausa até o limite: corta no bloco de 100 ms mais quieto do intervalo."""
        passo = TAXA // 10
        trecho = audio[inicio:fim]
        n = len(trecho) // passo
        energia = (trecho[: n * passo].reshape(n, passo) ** 2).mean(axis=1)
        return inicio + int(energia.argmin()) * passo + passo // 2

    def _entregar(self, ate: int, tem_fala: bool = True):
        if ate <= self._despachado:
            return
        audio = np.frombuffer(self._buf, dtype=np.int16, count=ate - self._despachado,
                              offset=self._despachado * 2).astype(np.float32) / 32768.0
        self._despachado = ate
        if tem_fala:
            log.info("trecho de %.1f s entregue", len(audio) / TAXA)
            self._ao_trecho(audio)
