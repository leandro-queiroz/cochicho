# ditado

Ditado por voz local para o GNOME no Wayland. Aperte **Ctrl+Shift+Espaço**, fale, aperte de novo: o texto é transcrito pelo Whisper no seu computador e colado onde está o cursor (terminal, Claude Code, navegador, e-mail). Nada sai da máquina e não há janela: o estado aparece nos avisos do GNOME.

> **In English:** a small, local voice-dictation tool for GNOME on Wayland, tuned for Brazilian Portuguese. Press a GNOME shortcut, speak, press it again: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) transcribes on your CPU and the text is pasted at the cursor through the xdg-desktop-portal RemoteDesktop interface. No cloud, no window, no telemetry. Long dictations are transcribed in chunks while you speak, so you only wait for the last one.

## Como funciona

- **Servidor** (`ditado servidor`, serviço `ditado.service` do systemd do usuário): carrega o `faster-whisper` uma vez e fica ouvindo o socket `$XDG_RUNTIME_DIR/ditado.sock`. Com o `large-v3-turbo`, usa uns 1,9 GB de RAM.
- **Atalho**: o Wayland não deixa um app comum capturar tecla global, então quem captura é o GNOME (atalho personalizado) e o comando `ditado alternar` só avisa o servidor.
- **Gravação**: `pw-record` (PipeWire), microfone padrão do sistema, 16 kHz mono.
- **Trechos em segundo plano**: o Whisper cobra quase o mesmo por uma janela de 30 s cheia ou vazia. Num ditado longo, cada trecho fecha numa pausa da fala (detectada pelo VAD Silero, depois de 8 s e antes de 28 s) e é transcrito enquanto você ainda fala; ao parar, só falta o último. 60 s de fala esperaram 5,4 s depois de parar, em vez de uns 16 s.
- **Colar**: o texto vai para a área de transferência (`wl-copy`) e o servidor aperta a tecla de colar pelo portal RemoteDesktop (`ditado/portal.py`). Na primeira vez o GNOME pergunta se permite o controle do teclado; a permissão fica guardada em `~/.local/share/ditado/portal-token`. Se a colagem falhar, o texto continua copiado e o aviso diz isso.
- **Avisos**: "Gravando…" fica na tela, com o botão **Cancelar**; depois "Transcrevendo…" e "Colado", com o começo do texto.

## Desempenho

Medido num i7-1165G7 (4 núcleos), 4 threads, `int8`, fala em português:

| Modelo | Espera por janela de até 30 s | Qualidade |
|---|---|---|
| `small` | ~2 s | troca palavras |
| `medium` | ~5 s | boa, pontua menos |
| `large-v3-turbo` (padrão) | ~5,5 s | a melhor, com pontuação |

Use o número de núcleos físicos em `threads`: neste processador, 4 threads rendem mais que 8.

## Requisitos

Testado no Ubuntu 26.04 com GNOME 50 no Wayland. Precisa de:

- `pw-record` (pacote `pipewire-bin`), `wl-copy` (`wl-clipboard`) e o PyGObject do sistema (`python3-gi`): `sudo apt install pipewire-bin wl-clipboard python3-gi`;
- [`uv`](https://docs.astral.sh/uv/), para criar o ambiente Python;
- o `xdg-desktop-portal-gnome` com a interface RemoteDesktop (vem no Ubuntu com GNOME).

Na primeira execução o modelo escolhido é baixado da Hugging Face (o `large-v3-turbo` tem uns 1,6 GB). Depois disso funciona sem internet.

## Instalar

```
git clone https://github.com/leandro-queiroz/ditado.git ~/Projetos/ditado
cd ~/Projetos/ditado
./instalar.sh                           # atalho Ctrl+Shift+Espaço
ATALHO='<Super>h' ./instalar.sh         # ou outro atalho
.venv/bin/ditado testar-colar           # em 3 s cola uma frase de teste; na 1ª vez pede a permissão
```

O instalador cria o `.venv` com o Python do sistema e `--system-site-packages` (o servidor usa o PyGObject do sistema para falar com o GNOME), copia `config.exemplo.toml` para `~/.config/ditado/config.toml` se ainda não existir, instala e liga o serviço e cria (ou reaproveita) o atalho do GNOME. Pode rodar de novo a qualquer momento.

## Usar

| Comando | O que faz |
|---|---|
| `ditado alternar` | começa a gravar; se já grava, para, transcreve e cola (é o que o atalho chama) |
| `ditado cancelar` | descarta o ditado em curso |
| `ditado ultimo` | copia de novo o último texto |
| `ditado historico [n]` | últimos ditados, com a duração e a espera |
| `ditado estado` | estado do servidor e do modelo |
| `ditado testar-colar` | cola uma frase de teste daqui a 3 s |

Logs: `journalctl --user -u ditado -f` (só tempos e tamanhos, nunca o texto). Reiniciar: `systemctl --user restart ditado`.

## Configurar

`~/.config/ditado/config.toml` (modelo comentado em [`config.exemplo.toml`](config.exemplo.toml)). Vale a partir do próximo ditado; trocar `modelo` ou `threads` recarrega o modelo sozinho.

- `colar_com`: `"ctrl+shift+v"` (padrão: terminal e navegador; no Chrome cola como texto simples), `"ctrl+v"` (LibreOffice, VS Code) ou `"nenhum"` (só copia). O Wayland não deixa saber qual janela está em foco, então a tecla é uma só.
- `contexto`: frase passada ao Whisper como `initial_prompt`. Com ela a pontuação sai bem melhor e os nomes citados saem escritos certo: ponha os termos, nomes e siglas que você mais fala.
- `[substituicoes]`: trocas depois da transcrição, por palavra inteira, sem diferenciar maiúsculas (`"cloud code" = "Claude Code"`).
- `historico = false` para não guardar os textos em `~/.local/share/ditado/historico.jsonl`.

## Desinstalar

```
systemctl --user disable --now ditado
rm ~/.config/systemd/user/ditado.service ~/.local/share/applications/ditado.desktop
```

Depois apague o atalho "Ditado" em Configurações › Teclado › Atalhos personalizados. A configuração e o histórico ficam em `~/.config/ditado/` e `~/.local/share/ditado/`; o modelo, no cache da Hugging Face (`~/.cache/huggingface/hub/`).

## Limitações

- Feito para o GNOME. Em outros ambientes com Wayland (KDE, wlroots) o portal e o `wl-copy` devem funcionar, mas o instalador só cria o atalho no GNOME e não foi testado lá.
- Só CPU. A espera de uns 5 s por janela de 30 s é o limite de um notebook; com GPU NVIDIA o `faster-whisper` é bem mais rápido, mas o ditado ainda não tem essa opção.
- `faster_whisper.decode_audio` quebra com o PyAV 15 ou mais novo (`metadata_errors`). O ditado não usa: entrega o áudio já como array.

## Licença

[MIT](LICENSE). Usa o [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT) e os modelos Whisper da OpenAI (MIT), baixados na primeira execução.
