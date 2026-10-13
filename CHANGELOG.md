# Changelog

## 0.3.0 — 2026-10-14

- Replace persistent jobs and resume with one temporary working directory per invocation.
- Normalize every input to M4A/AAC before upload, independent of file extension.
- Split only oversized or backend-rejected parts and publish output after full success.
- Reduce the CLI to input, output, format, language, and auth file.
- Add a versioned wheel installation path for agents.

## 0.2.0 — 2026-10-13

- Install PyAV and a platform-specific FFmpeg binary as Python dependencies.
- Remove the system FFmpeg and FFprobe requirement.
- Probe media in-process through PyAV.

## 0.1.0 — 2026-10-13

- Initial cross-platform Python package.
- Direct Codex ChatGPT authentication and transcription backend support.
- Automatic FFmpeg chunking with overlap-aware merging.
- Concurrent requests, retries, persistent state, and resume.
- Unix-style text and JSON output contracts.
