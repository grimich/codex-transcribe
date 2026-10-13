# `codex-transcribe`: финальная спецификация v1

Статус: ready for implementation  
Тип: одноцелевой Unix-подобный CLI  
Runtime: Python 3.11+  
Платформы: Windows, macOS, Linux

## 1. Решение

Инструмент выполняет ровно одну операцию:

```text
audio bytes → transcript
```

Публичная поверхность — одна команда:

```bash
codex-transcribe [OPTIONS] INPUT
```

Где `INPUT` — путь к аудиофайлу или `-` для stdin.

В v1 отсутствуют:

- сервер;
- daemon;
- GUI;
- запись с микрофона;
- горячие клавиши;
- вставка текста;
- пользовательские подкоманды split, inspect, status или clean;
- стабильный публичный Python SDK.

FFprobe, FFmpeg, разбиение, повторы, resume и сборка являются внутренними стадиями одной команды.

## 2. Инварианты

Реализация обязана соблюдать следующие правила:

1. `stdout` содержит только запрошенный результат.
2. Прогресс, предупреждения и ошибки идут только в `stderr`.
3. При ошибке возвращается ненулевой exit code.
4. Неполный результат не выдаётся без `--allow-partial`.
5. Части собираются строго в порядке исходного аудио.
6. OAuth token никогда не попадает в вывод, state или журналы.
7. Resume не использует результат от другого содержимого файла.
8. Исходный файл никогда не изменяется.
9. Запись через `-o` атомарна.
10. Два процесса не обрабатывают одну и ту же задачу одновременно.

## 3. Runtime и зависимости

### 3.1 Python

- CPython `>=3.11`.
- Установка через `pipx` или `uv tool`.
- Собственный `.exe` не собирается.

### 3.2 Python dependencies

Runtime:

```text
httpx
platformdirs
```

CLI строится на стандартном `argparse`. Модели и JSON реализуются стандартными `dataclasses`, `enum` и `json`. Это уменьшает размер dependency tree.

Development:

```text
pytest
pytest-asyncio
respx
```

### 3.3 Media dependencies

Требуются внешние:

```text
ffmpeg
ffprobe
```

Порядок поиска:

1. Явный `--ffmpeg` или `--ffprobe`.
2. `CODEX_TRANSCRIBE_FFMPEG` или `CODEX_TRANSCRIBE_FFPROBE`.
3. `PATH`.

Инструмент не скачивает FFmpeg автоматически.

## 4. CLI contract

### 4.1 Синтаксис

```text
codex-transcribe [OPTIONS] INPUT
```

Примеры:

```bash
codex-transcribe meeting.m4a > meeting.txt
```

```bash
codex-transcribe --format json meeting.m4a | jq -r '.text'
```

```bash
codex-transcribe --timestamps -o meeting.txt meeting.m4a
```

```bash
cat meeting.m4a | codex-transcribe - > meeting.txt
```

### 4.2 Опции

| Опция | Default | Значение |
|---|---:|---|
| `-o, --output PATH` | stdout | Записать результат в файл |
| `--format text\|json` | `text` | Формат результата |
| `--language CODE` | `ru` | Подсказка языка backend |
| `--timestamps` | off | Добавить интервалы частей в text output |
| `--max-upload-bytes N` | `20971520` | Максимальный размер одной загрузки |
| `--chunk-seconds N` | `600` | Верхний предел длительности части |
| `--overlap-seconds N` | `1.5` | Перекрытие соседних частей |
| `-j, --jobs N` | `3` | Одновременные запросы, `1..16` |
| `--retries N` | `3` | Число повторов временной ошибки |
| `--timeout N` | `900` | Общий timeout одного запроса |
| `--auth-file PATH` | auto | Codex auth JSON |
| `--state-dir PATH` | platformdirs | Каталог состояния |
| `--no-resume` | off | Не использовать сохранённые части |
| `--keep-work` | off | Сохранить аудиочасти после успеха |
| `--allow-partial` | off | Разрешить неполный результат |
| `--quiet` | off | Отключить обычный stderr progress |
| `--ffmpeg PATH` | auto | Путь к FFmpeg |
| `--ffprobe PATH` | auto | Путь к FFprobe |
| `--version` | — | Версия инструмента |

### 4.3 Валидация CLI

- `INPUT` обязателен, кроме `--version`.
- `--timestamps` допустим только с `--format text`.
- `jobs` находится в диапазоне `1..16`.
- `chunk-seconds > overlap-seconds * 2`.
- `max-upload-bytes >= 1 MiB`.
- Пустой stdin является ошибкой входа.

## 5. Семантика потоков

### 5.1 stdout

Без `-o`:

- `text` печатает только объединённый текст;
- `json` печатает один корректный JSON document;
- в конце добавляется ровно один newline;
- до успешной сборки в stdout ничего не пишется.

С `-o` stdout остаётся пустым.

### 5.2 stderr

stderr может содержать:

```text
Inspecting input: 00:52:46, 48.3 MiB
Splitting: 6 chunks
Transcribing: 4/6
Completed
```

`--quiet` скрывает progress и предупреждения, но не финальную ошибку.

Если stderr не является TTY, используются обычные строки без control sequences.

### 5.3 Атомарная выдача

- stdout заполняется только после готовности результата.
- Для `-o` сначала создаётся файл рядом с target: `.<name>.tmp-<random>`.
- После `flush` и `fsync` temporary file заменяет target через atomic rename.
- При сбое существующий target не повреждается.

## 6. Processing pipeline

Один запуск выполняет стадии:

```text
resolve input
→ fingerprint
→ probe
→ plan chunks
→ materialize chunks
→ transcribe
→ merge overlaps
→ render
→ atomic output
→ cleanup
```

### 6.1 Resolve input

Файл:

- путь разрешается в абсолютный;
- сохраняются `size`, `mtime_ns` и file identity, если доступна;
- проверяется право чтения.

stdin:

- поток сохраняется в private temporary file;
- SHA-256 считается во время записи;
- FFprobe запускается только после полного сохранения;
- temporary file удаляется после завершения, если не нужен resume.

### 6.2 Fingerprint

Для корректного resume используется полный SHA-256 содержимого.

Для файла stat читается до и после хеширования. Если `size`, `mtime_ns` или file identity изменились, обработка прекращается с ошибкой `input_changed`.

Job key:

```text
SHA-256(
  schema_version
  + source_sha256
  + language
  + chunk_seconds
  + max_upload_bytes
  + overlap_seconds
)
```

Concurrency, timeout, retries, output path и output format не входят в job key.

### 6.3 Probe

FFprobe вызывается с JSON output. Извлекаются:

- container format;
- audio codec;
- duration;
- bit rate;
- sample rate;
- channels;
- количество audio streams.

Правила:

- используется первый audio stream;
- отсутствие audio stream — ошибка media;
- неконечная, отрицательная или отсутствующая duration — ошибка media;
- video streams игнорируются;
- source повторно проверяется на изменение после probe.

Поддерживаемые входы v1:

```text
m4a/mp4 audio, mp3, wav, flac, ogg, webm
```

### 6.4 Chunk planning

Файл отправляется целиком, если:

- `size <= max_upload_bytes`;
- duration `<= chunk_seconds`;
- backend ранее не отклонил этот размер.

Иначе создаётся план частей.

Предварительная длительность:

```text
target = min(
  chunk_seconds,
  floor(max_upload_bytes * 8 / measured_bit_rate * 0.80)
)
```

Если bit rate отсутствует, используется `file_size * 8 / duration`.

После materialization фактический размер каждой части проверяется. Часть больше лимита рекурсивно делится пополам до выполнения ограничения.

Нет минимальной длительности части: ограничение размера важнее удобства.

### 6.5 Materialization

FFmpeg запускается без shell и получает аргументы массивом.

Предпочтительно используется stream copy:

```text
ffmpeg -ss START -i INPUT -t DURATION -map 0:a:0 -c copy OUTPUT
```

Требования:

- каждый chunk получает отдельный файл;
- фактическая duration измеряется FFprobe;
- logical interval не включает правое overlap соседней части;
- при невозможности stream copy v1 завершает работу ошибкой `unsupported_media`;
- автоматического перекодирования в v1 нет.

## 7. Границы частей и overlap

Разбиение без overlap способно ухудшить слово, попавшее на границу. Поэтому соседние части перекрываются на `overlap-seconds`.

Пример:

```text
chunk 0 media: 00:00.0–10:01.5
chunk 0 logical: 00:00.0–10:00.0

chunk 1 media: 09:58.5–20:01.5
chunk 1 logical: 10:00.0–20:00.0
```

### 7.1 Deduplication

Для пары соседних текстов:

1. Текст токенизируется по Unicode word boundaries.
2. Для сравнения применяются casefold и удаление окружающей пунктуации.
3. Ищется самый длинный точный suffix/prefix match размером от 3 до 40 токенов.
4. Совпавший prefix удаляется из второй части с сохранением её исходной пунктуации.
5. Если совпадение короче 3 токенов, оба текста сохраняются полностью.

При сомнении алгоритм допускает повтор, но не удаляет потенциально уникальный текст.

JSON chunk сохраняет исходный backend text и merged text отдельно.

## 8. Codex auth

### 8.1 Поиск auth-файла

Порядок:

1. `--auth-file`.
2. `CODEX_TRANSCRIBE_AUTH_FILE`.
3. `$CODEX_HOME/auth.json`.
4. `~/.codex/auth.json`.

### 8.2 Извлечение credentials

Auth reader поддерживает схемы текущего Codex auth JSON через изолированные schema adapters.

Результат:

```text
access_token: non-empty string
account_id: non-empty string
```

Account ID берётся из явного поля auth JSON. Если его нет, допускается чтение соответствующего claim из JWT payload без проверки подписи только потому, что сам JWT уже получен из доверенного локального auth-файла. `exp` проверяется до запроса.

Platform API key не принимается вместо ChatGPT OAuth token.

### 8.3 Работа с секретами

- Auth JSON не копируется в state dir.
- Token не сериализуется.
- Token и JWT-подобные строки редактируются в исключениях.
- Request headers не пишутся в debug output.
- Raw multipart body не журналируется.

## 9. Backend protocol

Default endpoint:

```text
POST https://chatgpt.com/backend-api/transcribe
```

Headers:

```text
Authorization: Bearer <access_token>
ChatGPT-Account-Id: <account_id>
```

Multipart fields:

```text
file=<audio bytes>
language=<language, если задан>
```

Поле `model` не отправляется.

Успешный ответ:

```json
{
  "text": "non-empty or empty string"
}
```

Требования клиента:

- `httpx.AsyncClient` переиспользуется для всех частей;
- учитываются стандартные proxy environment variables;
- redirects запрещены;
- максимальный размер response body — 16 MiB;
- content type и JSON schema проверяются;
- неизвестные поля ответа игнорируются;
- пустой `text` является успешным результатом с warning `empty_transcript`.

## 10. Scheduler и retries

### 10.1 Параллельность

- Используется `asyncio.Semaphore(jobs)`.
- Default `jobs=3`.
- Chunk results записываются сразу после успеха.
- Финальная сборка всегда сортирует по chunk index.

### 10.2 Классификация ошибок

Повторяются:

- connect/read/write timeout;
- connection reset;
- HTTP `408`;
- HTTP `429`;
- HTTP `500`, `502`, `503`, `504`.

Не повторяются как тот же запрос:

- HTTP `400` с ошибкой формата;
- HTTP `401` после одного обновления credentials;
- постоянный HTTP `403`;
- некорректный JSON в двух последовательных ответах;
- локальная ошибка FFmpeg.

Специальное поведение:

- `401`: перечитать auth-файл и повторить один раз;
- `413`: разделить конкретную часть пополам и продолжить;
- `429`: использовать `Retry-After`, но не больше 120 секунд;
- `5xx` на unsplit source: после обычных retries разделить source и продолжить;
- auth failure отменяет pending requests;
- ошибка одной части не отменяет уже запущенные независимые части, чтобы resume сохранил максимум работы.

Backoff без `Retry-After`:

```text
2s, 5s, 15s + random jitter 0..1000ms
```

## 11. State и resume

Default root определяется `platformdirs.user_state_dir("codex-transcribe")`.

Структура:

```text
<state-root>/<job-key>/
  lock
  manifest.json
  source.json
  input/                 # только для stdin
  chunks/
    0000.audio
    0000.response.json
    0000.error.json
  result.json
```

### 11.1 Manifest

Содержит:

- schema version;
- tool version;
- source SHA-256;
- source metadata без credentials;
- параметры, влияющие на job key;
- chunk plan;
- фактические media и logical intervals;
- status и attempts каждой части;
- warnings.

### 11.2 Atomic state

- Каждый JSON пишется через temporary file, flush, fsync и rename.
- Успешный response сохраняется до пометки chunk как completed.
- Повреждённый manifest не перезаписывается молча; возвращается `state_corrupt`.

### 11.3 Locking

- Перед изменением job state берётся exclusive lock.
- Если job уже выполняется, второй процесс завершается с exit code `8`.
- Stale lock определяется через PID и process start identity, а не только по возрасту файла.

### 11.4 Cleanup

- После полного успеха audio chunks удаляются, если не задан `--keep-work`.
- Manifest, responses и result сохраняются 7 дней для повторного вывода.
- Failed/incomplete state сохраняется 30 дней.
- Очистка выполняется best effort в начале запуска и не влияет на основную задачу.

## 12. Cancellation

На `SIGINT` и поддерживаемый платформой `SIGTERM`:

1. новые запросы не запускаются;
2. активным HTTP requests даётся до 5 секунд на завершение;
3. дочерние FFmpeg/FFprobe процессы завершаются;
4. manifest атомарно сохраняется;
5. process завершается с кодом `130` для SIGINT.

В stdout частичный текст не печатается.

## 13. Output formats

### 13.1 Text

Default:

```text
Первый фрагмент текста.

Следующий фрагмент текста.
```

С `--timestamps`:

```text
[00:00:00–00:10:00]
Первый фрагмент текста.

[00:10:00–00:20:00]
Следующий фрагмент текста.
```

Пустые части:

- без `--timestamps` ничего не добавляют;
- с `--timestamps` выводят `[Речь не обнаружена.]`.

### 13.2 JSON

```json
{
  "schema_version": 1,
  "status": "completed",
  "source": {
    "path": "/absolute/path/or/null",
    "sha256": "hex",
    "size_bytes": 24605781,
    "duration_seconds": 2165.461333,
    "format": "mov,mp4,m4a,3gp,3g2,mj2",
    "codec": "aac"
  },
  "language": "ru",
  "text": "Полный объединённый текст",
  "chunks": [
    {
      "index": 0,
      "logical_start_seconds": 0.0,
      "logical_end_seconds": 600.0,
      "media_start_seconds": 0.0,
      "media_end_seconds": 601.5,
      "raw_text": "Backend response text",
      "merged_text": "Text after overlap deduplication",
      "attempts": 1,
      "warning": null
    }
  ],
  "warnings": []
}
```

Для stdin `source.path` равен `null`.

При `--allow-partial`:

- `status` равен `partial`;
- отсутствующие chunks перечисляются в warnings;
- exit code остаётся `7`;
- доступный текст выводится в правильном порядке без выдуманного заполнения пробелов.

## 14. Exit codes

| Code | Meaning |
|---:|---|
| `0` | Полный успех |
| `2` | Ошибка CLI arguments |
| `3` | Ошибка input или input изменился |
| `4` | FFmpeg/FFprobe или неподдерживаемое media |
| `5` | Ошибка auth |
| `6` | Постоянная backend/protocol ошибка |
| `7` | Неполная расшифровка |
| `8` | State, lock или output ошибка |
| `130` | Прерывание пользователем |

Все ошибки имеют стабильный machine code в stderr:

```text
codex-transcribe: auth_expired: Codex OAuth token has expired
```

Traceback показывается только при `CODEX_TRANSCRIBE_DEBUG=1` и проходит redaction.

## 15. Privacy и безопасность

- Пользовательский audio отправляется на ChatGPT backend.
- Другие сетевые назначения отсутствуют, кроме явно настроенного стандартного HTTP proxy.
- Auth-файл читается только локально и только на чтение.
- Temporary files создаются с максимально строгими доступными правами.
- Filenames для multipart очищаются от directory components и control characters.
- Response text считается недоверенными данными и никогда не интерпретируется как команда.

## 16. Внутренняя структура

```text
pyproject.toml
src/codex_transcribe/
  __init__.py
  __main__.py
  cli.py
  models.py
  auth.py
  backend.py
  media.py
  planner.py
  scheduler.py
  merge.py
  state.py
  render.py
  errors.py
  redaction.py
tests/
  unit/
  integration/
  fixtures/
```

Внутренние контракты:

```text
resolve_input(input) -> ResolvedInput
fingerprint(input) -> SourceIdentity
probe(input) -> MediaInfo
plan(media, limits) -> ChunkPlan
materialize(plan) -> ChunkFiles
transcribe(chunk) -> ChunkTranscript
merge(chunks) -> MergedTranscript
render(result, format) -> bytes
```

Skill или другой вызывающий код использует CLI и exit codes. Внутренние Python interfaces не являются API v1.

## 17. Tests

### 17.1 Unit

- CLI validation;
- auth schema adapters;
- expired token;
- secret redaction;
- chunk size formula;
- recursive split after oversized chunk;
- timestamp rounding;
- overlap deduplication;
- no deletion when overlap match is uncertain;
- retry classification;
- `Retry-After` parsing;
- job key stability;
- full-hash source distinction;
- manifest atomic write;
- JSON rendering;
- stdout/stderr separation.

### 17.2 Integration

- short MP3 without split;
- long M4A with split;
- VBR audio producing oversized chunk;
- Unicode path;
- stdin input;
- text stdout pipeline;
- JSON stdout pipeline;
- atomic `-o` replacement;
- interruption and resume;
- concurrent second process lock;
- source modification during hashing;
- `401` followed by refreshed auth;
- `413` followed by smaller chunks;
- `429` with Retry-After;
- `5xx` retries;
- empty transcript;
- one permanent failed chunk with and without `--allow-partial`;
- no token in stderr, state or exception text.

HTTP tests используют `respx` или `httpx.MockTransport`; listening socket не создаётся.

### 17.3 Cross-platform CI

Обязательная матрица:

```text
Windows latest × Python 3.11, 3.12, 3.13
macOS latest   × Python 3.11, 3.12, 3.13
Ubuntu latest  × Python 3.11, 3.12, 3.13
```

Сетевые тесты настоящего приватного endpoint не запускаются в обычном CI.

## 18. Acceptance criteria

v1 готова, когда выполняются все условия:

1. `codex-transcribe short.mp3` выдаёт только текст в stdout.
2. Длинный M4A автоматически делится и собирается.
3. Ни один request body не превышает `max-upload-bytes`.
4. Фрагменты выводятся в исходном порядке при любой последовательности завершения requests.
5. Пограничная речь защищена overlap, а dedup не удаляет текст при неуверенном совпадении.
6. Прерывание сохраняет состояние, повторный запуск продолжает задачу.
7. Файл с тем же размером, но другим содержимым не использует старый state.
8. Два процесса не отправляют один chunk одновременно.
9. stdout остаётся валидным text или JSON pipeline output.
10. Existing output не повреждается при ошибке.
11. Token отсутствует во всех артефактах и диагностике.
12. MP3, M4A, WAV, FLAC, OGG и WebM покрыты integration fixtures.
13. Windows, macOS и Linux CI проходят.
14. Установка через `pipx` и `uv tool` создаёт команду `codex-transcribe`.
15. Skill может определить успех только по exit code и прочитать результат из stdout или заданного output-файла.

## 19. Осознанные ограничения v1

- Используется приватный и недокументированный ChatGPT endpoint.
- Изменение endpoint или auth schema может потребовать выпуска новой версии.
- Диаризации и speaker labels нет.
- Точных word timestamps нет.
- Автоматического исправления текста через LLM нет.
- Перекодирования неподдерживаемого audio нет.
- При отсутствии надёжного overlap match возможно небольшое повторение текста на границе; потеря уникального текста считается худшим исходом и запрещена.

## 20. Порядок реализации

### Milestone 1: один короткий файл

- package skeleton;
- argparse CLI;
- auth reader;
- direct backend client;
- text/JSON stdout;
- redaction.

### Milestone 2: длинное audio

- FFprobe;
- chunk planner;
- FFmpeg materialization;
- scheduler;
- overlap merge.

### Milestone 3: надёжность

- retries;
- full source fingerprint;
- manifest;
- locking;
- resume;
- cancellation;
- atomic output.

### Milestone 4: доставка

- cross-platform tests;
- wheel и sdist;
- pipx/uv installation;
- versioning и changelog.
