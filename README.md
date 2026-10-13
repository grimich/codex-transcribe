# codex-transcribe

One command: audio in, transcript out. The Python package decodes the input with its installed media dependencies and sends normalized audio to the transcription backend used by Codex.

## Requirements

- Python 3.10 or newer on Windows x86-64, macOS x86-64/arm64, or Linux x86-64
- An existing Codex ChatGPT login
- Network access to `chatgpt.com`

The package installs PyAV and a platform-specific FFmpeg binary. System Git, FFmpeg, FFprobe, and a compiler are not required.

## Install

Install the versioned wheel into a Python environment:

```bash
python -m pip install --only-binary=:all: "https://github.com/grimich/codex-transcribe/releases/download/v0.3.0/codex_transcribe-0.3.0-py3-none-any.whl"
codex-transcribe --version
```

`--only-binary=:all:` makes installation fail clearly if a required platform wheel is unavailable instead of compiling a dependency.

## Use

```bash
codex-transcribe --output recording.txt recording.m4a
codex-transcribe --output recording.json --format json recording.mp3
codex-transcribe --output video.txt video.mp4
cat recording.m4a | codex-transcribe --output recording.txt -
```

The media format is detected from the bytes. The filename extension can be arbitrary. If a video has several audio streams, the first is used. Empty, encrypted, damaged, and undecodable input fails with an error. A recording without recognized speech can produce an empty transcript.

Without `--output`, the result is written to stdout. Diagnostics go to stderr. The output file is replaced atomically only after the entire recording succeeds. The command rejects an output path that points to the input file, including a symlink or hardlink.

`--language CODE` optionally gives the backend a language hint. Without it, the backend detects the language. `--auth-file PATH` selects a Codex auth file.

JSON output has this schema:

```json
{"schema_version":1,"status":"completed","text":"...","duration_seconds":42.5}
```

Exit status `0` means the complete result was produced. Errors return a nonzero status and do not emit a partial transcript.

## Agent instruction

Give the agent the wheel URL above and this instruction:

```text
Install the versioned codex-transcribe wheel with Python pip and
--only-binary=:all:. Confirm codex-transcribe --version succeeds.
For AUDIO, run: codex-transcribe --output AUDIO.txt AUDIO
Return the output path on success or the command's error on failure.
Do not read or print auth.json.
```

The same CLI can be called from a skill; the skill needs no audio or network implementation.

## Authentication

The auth file is selected in this order:

1. `--auth-file`
2. `CODEX_TRANSCRIBE_AUTH_FILE`
3. `$CODEX_HOME/auth.json`
4. `~/.codex/auth.json`

The tool reads this file without modifying or copying it. Audio is uploaded to `https://chatgpt.com/backend-api/transcribe`. This is a private endpoint; protocol changes require a package update.

## Source installation

A source archive can be installed without Git:

```bash
python -m pip install "https://github.com/grimich/codex-transcribe/archive/refs/tags/v0.3.0.zip"
```

The versioned wheel above is the primary installation method.

## License

MIT
