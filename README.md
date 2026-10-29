# possess — a haunted terminal

![placeholder for demo GIF](docs/demo.gif)

> Your shell, haunted. After every command, a mournful spirit trapped in the
> machine whispers a one-sentence comment about what you just ran — generated
> by a **local, open-weight LLM** (Ollama) that never leaves your laptop.

"Ghost in the Machine" — Halloween / Día de los Muertos hackathon theme.

## Pitch

`possess` hooks into your interactive shell (bash or zsh) and, after each
command, a ghost whispers a short eerie comment about what just ran. The ghost
is powered by a local open-weight model (default `llama3.2:3b`, ~2 GB, runs on
a laptop GPU with ~6 GB VRAM) via Ollama, so **nothing ever leaves your machine
and it works with no internet**. It reacts to specifics: `rm -rf` is a murder,
`git push --force` is a cursed ritual, `sudo` is summoning, a failed command is
a death, `cd ..` is descending into the dark. Includes a Día de los Muertos
`ofrenda` mode that honors the commands that died this session.

## Install

```bash
git clone https://github.com/priyansh07-svnit/possess.git
cd possess
./install.sh
```

The installer:
- checks for Python 3 and Ollama,
- copies `possess.sh`, `possess_daemon.py`, and the `possess` CLI to
  `~/.local/share/possess/`,
- links `~/.local/bin/possess`,
- asks before adding a source line to your `.bashrc`/`.zshrc`,
- prints `ollama pull llama3.2:3b` if the model is missing.

Then restart your shell and start the ghost:

```bash
. ~/.local/share/possess/possess.sh
possess on
```

Run a command and wait for the whisper:

```
$ rm -rf old_project/
$ 
👻 You buried something today. I hope it stays buried.
$
```

## Usage

```
possess on           start the daemon & enable whispering
possess off          stop whispering
possess status       show enabled / haunt level / daemon state
possess haunt 1      subtle (rare whispers)
possess haunt 2      creepy (default)
possess haunt 3      full poltergeist (frequent whispers)
possess ofrenda      Día de los Muertos altar + ghost remembrance
possess ofrenda --lang es   same, in Spanish
```

Whispers are dimmed/italic, prefixed with `👻`, and print on the next prompt.
The hook is fully asynchronous: it sends the event to the daemon and returns
immediately, so it never slows your prompt.

## Configuration

Edit `~/.config/possess/config.toml` or set environment variables:

```toml
model = "llama3.2:3b"
endpoint = "http://localhost:11434/v1"
haunt = 2            # 1 = subtle, 2 = creepy, 3 = full poltergeist
lang = "en"          # "en" or "es"
socket = "$HOME/.local/share/possess/possess.sock"
```

Equivalent env vars: `POSSESS_MODEL`, `POSSESS_ENDPOINT`, `POSSESS_HAUNT`,
`POSSESS_LANG`, `POSSESS_SOCKET`, `POSSESS_MODEL_TIMEOUT` (default 3s).

To uninstall cleanly: `./uninstall.sh`.

## ASCII demo transcript

```
$ git push --force origin main
Enumerating objects: 5, done.
...
To github.com:priyansh07-svnit/possess.git
 ! [rejected]        main -> main (non-fast-forward)
... 
$ 
👻 You rewrote history. The machine remembers, even when you do not.
$ cargo deploy --to production
error: process didn't exit successfully
$ 
👻 It died. Again. I watched it happen.
$ possess ofrenda
        (ASCII candles, marigolds, a skull ...)
Here rest the commands that no longer run, the ones that died trying.
We honor cargo, fallen commands that tried to live in this terminal.
Rest in peace, little commands. The ghost remembers.
```

## Why open-weight and local matters here

- **Fully offline on a laptop.** The model runs locally through Ollama; no
  command is ever sent over the network. Works with no internet.
- **Your shell history never leaves your machine.** A terminal ghost reading
  your commands through a cloud API would be creepy in the wrong way. possess
  never logs commands to disk and redacts obvious secrets (tokens, passwords,
  `--password`, `export *KEY*=`, etc.) before anything goes to the model.
- **You own the ghost.** Swap the model, change the endpoint, or fine-tune the
  persona — it's just a local OpenAI-compatible endpoint and a system prompt.
- **Costs nothing to run.** No API keys, no metered calls.

Tradeoffs: small open-weight models (`llama3.2:3b`, `qwen2.5:3b`) are less
witty and consistent than frontier models, and on slower machines the model can
take a few seconds to answer. possess handles that gracefully: a hard ~3s
timeout with a silent drop, plus a built-in table of pre-written spooky
one-liners so the ghost still whispers even with zero model installed.

## License

MIT. See LICENSE.
