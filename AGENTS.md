# Agent instructions

This repository contains one Python CLI: `codex-transcribe`.

## Install

1. Use Python 3.10 or newer.
2. Install this repository with `pipx install .`, `uv tool install .`, or `python -m pip install .`.
3. Ensure `ffmpeg` and `ffprobe` are present in `PATH`.
4. Do not copy, print, or modify `~/.codex/auth.json`.

## Run

```bash
codex-transcribe -o recording.transcript.txt recording.m4a
```

Use `--format json` when machine-readable output is required. A zero exit status means the complete transcript was produced.

## Scope

The tool is a Unix-style filter. Do not add a server, daemon, GUI, microphone capture, clipboard integration, or unrelated commands.
