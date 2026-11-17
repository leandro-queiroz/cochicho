"""Caminhos e configuração: ~/.config/ditado/config.toml por cima dos valores padrão.

Sem dependências pesadas: o cliente do atalho também importa este módulo.
"""

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ditado"
DADOS_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "ditado"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")

ARQUIVO_CONFIG = CONFIG_DIR / "config.toml"
SOCKET = RUNTIME_DIR / "ditado.sock"
HISTORICO = DADOS_DIR / "historico.jsonl"
TOKEN_PORTAL = DADOS_DIR / "portal-token"


@dataclass
class Config:
    modelo: str = "large-v3-turbo"
    threads: int = 4
    beam: int = 5
    colar_com: str = "ctrl+shift+v"
    sons: bool = True
    historico: bool = True
    max_segundos: int = 300
    contexto: str = "Ditado em português do Brasil, com pontuação."
    substituicoes: dict[str, str] = field(default_factory=dict)


class ConfigInvalida(Exception):
    pass


def carregar() -> Config:
    """Lê o config.toml. Chave desconhecida ou de tipo errado vira ConfigInvalida, com a mensagem para o aviso."""
    cfg = Config()
    if not ARQUIVO_CONFIG.exists():
        return cfg
    try:
        with open(ARQUIVO_CONFIG, "rb") as f:
            dados = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigInvalida(f"{ARQUIVO_CONFIG}: {e}") from e
    tipos = {f.name: type(getattr(cfg, f.name)) for f in fields(cfg)}
    for chave, valor in dados.items():
        if chave not in tipos:
            raise ConfigInvalida(f"{ARQUIVO_CONFIG}: chave desconhecida '{chave}'")
        if not isinstance(valor, tipos[chave]) or (isinstance(valor, bool) and tipos[chave] is not bool):
            raise ConfigInvalida(f"{ARQUIVO_CONFIG}: '{chave}' deveria ser {tipos[chave].__name__}")
        setattr(cfg, chave, valor)
    return cfg
