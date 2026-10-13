# codex-transcribe

A small Unix-style Python CLI that transcribes audio through the private ChatGPT backend used by Codex Desktop. It reuses your existing Codex login from `~/.codex/auth.json`.

```bash
codex-transcribe meeting.m4a > meeting.txt
```

The tool has one job: audio in, transcript out. There is no server, daemon, GUI, microphone recorder, clipboard integration, or public API service.

## Features

- Windows, macOS, and Linux
- MP3, M4A, WAV, FLAC, OGG, and WebM
- automatic splitting of large recordings
- parallel transcription with retries
- overlap-aware chunk merging
- resumable work after interruption
- clean stdout and diagnostic stderr
- plain text or structured JSON
- atomic output files
- direct use of the Codex ChatGPT login; no developer API key

## Requirements

- Python 3.10 or newer
- an active ChatGPT login in Codex
- FFmpeg and FFprobe available in `PATH`

Install FFmpeg:

### Windows

```powershell
winget install --id BtbN.FFmpeg.LGPL -e
```

Open a new terminal after installation.

### macOS

```bash
brew install ffmpeg
```

### Debian or Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

## Install

With `pipx`:

```bash
pipx install git+https://github.com/grimich/codex-transcribe.git@v0.1.0
```

With `uv`:

```bash
uv tool install git+https://github.com/grimich/codex-transcribe.git@v0.1.0
```

With pip:

```bash
python -m pip install git+https://github.com/grimich/codex-transcribe.git@v0.1.0
```

## Usage

Plain text to stdout:

```bash
codex-transcribe recording.m4a > recording.txt
```

Atomic output file:

```bash
codex-transcribe -o recording.txt recording.m4a
```

Add chunk timestamps:

```bash
codex-transcribe --timestamps recording.m4a
```

Structured JSON:

```bash
codex-transcribe --format json recording.m4a > recording.json
```

Use in a pipeline:

```bash
codex-transcribe recording.m4a | your-next-tool
```

Read audio from stdin:

```bash
cat recording.m4a | codex-transcribe - > recording.txt
```

Russian is the default language hint. Override it when needed:

```bash
codex-transcribe --language en interview.mp3
```

## Agent installation

Give an agent this repository URL and the following instruction:

```text
Install codex-transcribe from this GitHub repository using pipx, uv tool, or
python -m pip. Ensure ffmpeg and ffprobe are available in PATH. Then run:

codex-transcribe -o <audio-name>.transcript.txt <audio-file>
```

The agent does not need to read or copy access tokens. The CLI locates Codex authentication in this order:

1. `--auth-file`
2. `CODEX_TRANSCRIBE_AUTH_FILE`
3. `$CODEX_HOME/auth.json`
4. `~/.codex/auth.json`

## Output contract

- stdout contains only the requested text or JSON
- progress and errors go to stderr
- no partial transcript is emitted unless `--allow-partial` is used
- output written with `-o` is replaced atomically
- exit status `0` means complete success

Important exit codes:

| Code | Meaning |
|---:|---|
| 0 | complete success |
| 2 | invalid CLI arguments |
| 3 | invalid or changing input |
| 4 | FFmpeg or media failure |
| 5 | Codex authentication failure |
| 6 | backend failure |
| 7 | partial or incomplete transcript |
| 8 | state, lock, or output failure |
| 130 | interrupted by the user |

## Privacy and stability

Audio is uploaded to `https://chatgpt.com/backend-api/transcribe` using the OAuth token already stored by Codex. Tokens are never written to transcript files or state files.

This endpoint is private and undocumented. It may change without notice. The backend-specific code is isolated in `backend.py` so it can be updated independently from splitting, resume, and output behavior.

## Development

```bash
git clone https://github.com/grimich/codex-transcribe.git
cd codex-transcribe
python -m venv .venv
```

Activate the virtual environment and install the package:

```bash
python -m pip install -e .
codex-transcribe --help
```

## License

MIT

