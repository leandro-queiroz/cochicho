"""faster-whisper em CPU, com o modelo carregado uma vez e mantido na memória."""

import logging
import re
import threading
import time

import numpy as np
from faster_whisper import WhisperModel

log = logging.getLogger(__name__)

TAXA_AQUECIMENTO = 8000  # meio segundo a 16 kHz

# Frases que o Whisper inventa em silêncio ou ruído (vêm das legendas com que foi treinado).
ALUCINACOES = {
    "legendas pela comunidade amara.org",
    "obrigado por assistir",
    "obrigada por assistir",
    "inscreva-se no canal",
    "legenda adriana zanotto",
    "tchau, tchau",
}


def _normalizar(texto: str) -> str:
    return re.sub(r"[\s.!?,…]+", " ", texto.lower()).strip()


class Transcritor:
    def __init__(self):
        self._modelo = None
        self.chave = None  # (modelo, threads) carregados ou em carga
        self._pronto = threading.Event()
        self._trava = threading.Lock()

    @property
    def carregando(self) -> bool:
        return not self._pronto.is_set()

    def carregar_em_segundo_plano(self, nome: str, threads: int):
        self.chave = (nome, threads)
        self._pronto.clear()
        threading.Thread(target=self._carregar, args=(nome, threads), name="modelo", daemon=True).start()

    def _carregar(self, nome: str, threads: int):
        t = time.monotonic()
        with self._trava:
            self._modelo = None
            try:
                modelo = WhisperModel(nome, device="cpu", compute_type="int8", cpu_threads=threads,
                                      local_files_only=True)
            except Exception:  # noqa: BLE001 - modelo fora do cache: baixa da Hugging Face
                log.info("modelo %s fora do cache, baixando", nome)
                modelo = WhisperModel(nome, device="cpu", compute_type="int8", cpu_threads=threads)
            # a primeira inferência é mais lenta: aquece com meio segundo de silêncio
            list(modelo.transcribe(np.zeros(TAXA_AQUECIMENTO, dtype=np.float32), language="pt")[0])
            self._modelo = modelo
        self._pronto.set()
        log.info("modelo %s pronto em %.1f s (%d threads)", nome, time.monotonic() - t, threads)

    def transcrever(self, audio: np.ndarray, contexto: str, anterior: str, beam: int) -> str:
        self._pronto.wait()
        prompt = " ".join(p for p in (contexto.strip(), anterior[-300:].strip()) if p) or None
        t = time.monotonic()
        with self._trava:
            segmentos, _ = self._modelo.transcribe(
                audio,
                language="pt",
                beam_size=beam,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                without_timestamps=True,
                condition_on_previous_text=False,
                initial_prompt=prompt,
            )
            partes = [
                s.text.strip()
                for s in segmentos
                if s.text.strip() and not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)
            ]
        texto = " ".join(partes)
        if _normalizar(texto) in ALUCINACOES:
            texto = ""
        log.info("trecho de %.1f s transcrito em %.1f s", len(audio) / 16000, time.monotonic() - t)
        return texto
