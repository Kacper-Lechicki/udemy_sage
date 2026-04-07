# udemy-sage guide

This document explains **what the tool does**, **how to use it from a user perspective**, and **how it is built under the hood** — in enough detail to be useful, but without requiring you to read every line of the codebase.

For install, CLI flags, and security/legal disclaimers, see [README.md](../README.md).

---

## 1. What is it for?

**udemy-sage** is a self-contained command-line (CLI) application that:

1. Talks to the official **Udemy API** on your behalf (using your browser session).
2. Downloads **English** subtitle tracks (WebVTT) for lessons in a course you have purchased.
3. For each lesson that has a transcript, sends the text to a **language model** (OpenAI, Anthropic, Gemini, OpenRouter, or local Ollama).
4. Writes **Markdown notes** with a fixed section layout and a **Dataview** index into a folder you choose (your Obsidian vault).

In one line: **Udemy course → transcripts → AI → consistent notes in your vault**.

---

## 2. High-level view

```
Course URL
    → browser cookies (browser-cookie3)
    → Udemy API (JSON: course, curriculum, caption URLs)
    → download .vtt files (HTTPS, allowlisted hosts only)
    → VTT parser → plain transcript text
    → AI client (retry, backoff) → note body
    → renderer → .md files + Dataview index
```

Module boundaries in the code are intentionally sharp: each file has a narrow responsibility, which keeps tests and refactors manageable.

| Module         | Role                                                                                 |
| -------------- | ------------------------------------------------------------------------------------ |
| `config.py`    | `~/.udemy-sage/config.json`, validation, file permissions, cost estimates, error log |
| `fetcher.py`   | Cookies, Udemy API requests, VTT download, building the `Course` object              |
| `parser.py`    | Data models (`Course`, `Section`, `Lesson`), VTT parsing, `slugify`, lesson duration |
| `ai_client.py` | One shared SDK client per run (except Ollama), provider adapters, retries            |
| `renderer.py`  | Markdown with front matter, skip-if-exists, Dataview index                           |
| `cli.py`       | Setup wizard, URL validation, progress bar, parallel generation, cost summary        |

Entry point: `udemy-sage` → `udemy_sage.cli:main` (defined in `pyproject.toml`).

---

## 3. User flow (CLI)

1. **Parse arguments** (`--reconfigure`, `--reconfig=FIELD`, `--ollama-base-url`).
2. **Configuration**: if missing or full reconfigure — interactive wizard (`questionary`); single field — `_reconfigure_field`.
3. **Course URL**: must use `https://` and host `udemy.com` (validator in `cli.py`).
4. **Checks**: vault directory must exist; API key required unless provider is Ollama.
5. **`build_course(url, cookies_browser)`** — fetch structure and subtitles.
6. **Estimate**: transcript characters → rough token count (`// 4`) → `estimate_cost` using `config.COST_PER_M_TOKENS`.
7. **User confirms** whether to proceed.
8. **`_generate_all_notes`**: up to five threads (`ThreadPoolExecutor`), shared AI client (`build_shared_client`), `lesson.transcript` is **overwritten** with the generated note (Markdown with `##` headings from the model).
9. **`render_course`**: write files; existing files are **skipped** (not overwritten).
10. **Summary**: tokens, cost, AI failures (details in `errors.log`).

---

## 4. Configuration (`config.py`)

- Directory: `~/.udemy-sage/`, `config.json` written with `chmod 600` where supported, directory `700` (on Windows some steps may be no-ops).
- **Providers**: `openai`, `anthropic`, `gemini`, `openrouter`, `ollama`.
- **Default models** live in `DEFAULT_MODELS`; the user can change them.
- **`COST_PER_M_TOKENS`** are rough USD rates per 1M tokens (pre/post-run estimates — not invoices from the vendor).
- **`validate_config`**: requires provider, model, vault path; API key for all providers except Ollama; optionally validates Ollama base URL (`http`/`https` + host).
- **`log_error`**: appends a line to `errors.log` — lesson id + message, **no** transcripts or keys.
- Corrupt JSON on `load_config` is moved to a timestamped backup filename.

---

## 5. Fetching from Udemy (`fetcher.py`)

### 5.1 Authentication

- **browser-cookie3** loads `.udemy.com` cookies from the selected browser (`chrome`, `firefox`, …).
- The **`access_token`** cookie must be present — if you are not logged into Udemy in that browser, the tool fails with a clear error.
- API request headers: `Authorization: Bearer <access_token>`, full `Cookie` header (as in the browser), plus `User-Agent` and `Accept: application/json`.

### 5.2 API

- Base URL: `https://www.udemy.com/api-2.0`.
- **Slug** is parsed from the course URL (`/course/<slug>/`).
- **`/courses/{slug}/`** — course `id` and `title`.
- **`/courses/{id}/subscriber-curriculum-items/`** — pagination (`page_size=200`, `next` link), items of type `chapter` (sections) and `lecture` (lessons).
- For each lecture, `asset.captions` is scanned for the first track whose locale starts with `en` (English captions).

### 5.3 VTT subtitles

- Caption URLs must pass **`_is_allowed_caption_url`**: **HTTPS** only, host `*.udemy.com` or `*.udemycdn.com` (defense in depth against odd API data).
- Files download into a **`tempfile.TemporaryDirectory`**; when `build_course` returns, that directory is removed.
- Failed downloads for a single VTT are logged as warnings; if **no** English track succeeds or the course has no captions, the code raises **`FetchError`**.

### 5.4 In-memory model

- `Course` gets `title`, `url`, `url_slug` (URL slug — used for vault folder names).
- `Section` has index and title; `Lesson` has index, title, `transcript` (at this stage: text from VTT), `duration` from the last VTT timestamp or `"unknown"`.
- If the curriculum starts with a lecture before any chapter, a synthetic **"General"** section is created.

---

## 6. Parsing and models (`parser.py`)

- **`slugify`**: Unicode normalization, ASCII, lowercase, filesystem-safe characters.
- **`parse_vtt`**: strips WEBVTT header, cue ids, timestamp lines, HTML-like tags (e.g. `<c>`), **deduplicates** consecutive identical lines (common in subtitles).
- **`estimate_duration`**: last timestamp → approximate minutes.
- **`Lesson.filename`**: `{index:03d}_{title_slug}.md`.
- **`Section.dirname`**: `{index:02d}_{section_slug}`.
- **`Course.slug`**: prefers `url_slug`, otherwise title — consistent with [README.md](../README.md) (`resources/udemy/<slug>/`).

---

## 7. AI layer (`ai_client.py`)

### 7.1 System prompt

All providers receive the same skeleton for the expected Markdown response:

- Summary, Key concepts, Code examples, Insights, Questions to reflect on.

The user message is: lesson title + full transcript.

### 7.2 Providers

| Provider   | Implementation                                                            |
| ---------- | ------------------------------------------------------------------------- |
| OpenAI     | `openai.OpenAI`, `chat.completions`                                       |
| OpenRouter | Same SDK, `base_url=https://openrouter.ai/api/v1`                         |
| Anthropic  | `Anthropic.messages`, system prompt separate                              |
| Gemini     | `google.genai` `generate_content` (system + user in one string)           |
| Ollama     | **No** extra package — `urllib` to `{base}/api/chat`, JSON, 300 s timeout |

### 7.3 Sharing and parallelism

- For cloud APIs, **one** client instance (`build_shared_client`) is passed into many `generate_note` calls — lower overhead than a new client per lesson.
- Ollama returns `None` as the client; each call uses HTTP separately (with configurable `base_url`).

### 7.4 Retries

- Up to **4** retries (`MAX_RETRIES`), exponential backoff (`BACKOFF_BASE ** attempt`).
- Retries cover **429**, **5xx**, and typical network/timeout errors from httpx/OpenAI/Anthropic/Gemini (when those imports exist).
- After exhausting retries — `AIError` with the last message.

### 7.5 Tokens and cost

- Token counts come from API responses (Ollama: `eval_count` + `prompt_eval_count` from JSON).
- `estimate_cost` multiplies by a per-provider constant — **ballpark**; it does not model per-model input/output pricing.

---

## 8. Rendering to Obsidian (`renderer.py`)

- Base path: `<vault>/resources/udemy/<course.slug>/`.
- For each lesson with **non-empty** `lesson.transcript` (after AI, this is the note Markdown): write a file with **YAML front matter** (`course`, `section`, `lesson`, `duration`, `generated`) and `parent:: [[course_slug]]`.
- **Idempotency**: if the file already exists, the lesson is listed in `skipped` and not overwritten.
- No transcript: log to `errors.log`, no file created.
- **`_render_index`**: `{course.slug}.md` with a Dataview block (`TABLE section, lesson, duration`) and a manual list of sections and wikilinks; lessons without subtitles are marked in the list.

---

## 9. Security and privacy (summary)

- API keys live only in the user config file with restrictive permissions; they are not written to the error log.
- Subtitle downloads use **HTTPS** to trusted Udemy hosts only.
- Temporary VTT files go away with the temp directory.
- Ollama: default traffic stays local; another host is an explicit user choice.

For the full checklist, see the **Security** section in [README.md](../README.md).

---

## 10. Dependencies and install

- **Core**: `browser-cookie3`, `questionary`, `rich`.
- **Optional** (extras in `pyproject.toml`): `openai`, `anthropic`, `google-genai` — e.g. `pipx install ".[all]"` or a single extra.

---

## 11. Limitations and legal awareness

- You need a **purchased** course and an active browser session.
- **English** captions only (first matching `en*` track).
- Use of the Udemy API and derivative notes may be governed by Udemy’s terms — see the disclaimer in [README.md](../README.md).
- Cost figures are estimates; actual billing depends on the provider and model.

---

## 12. Where to look in the code

| Topic                          | File / symbol                                                    |
| ------------------------------ | ---------------------------------------------------------------- |
| CLI flags, wizard, parallelism | `cli.py` — `main`, `_generate_all_notes`, `MAX_PARALLEL_NOTES`   |
| API endpoints, cookies, VTT    | `fetcher.py` — `build_course`, `_api_get`, `_download_vtt`       |
| Course structure, VTT → text   | `parser.py` — `Course`, `parse_vtt`                              |
| Prompts, retry, providers      | `ai_client.py` — `SYSTEM_PROMPT`, `generate_note`, `_with_retry` |
| File output, Dataview          | `renderer.py` — `render_course`, `_render_index`                 |
| Config, cost                   | `config.py` — `validate_config`, `COST_PER_M_TOKENS`             |

Together with [README.md](../README.md), this should be enough to understand the **full pipeline from URL to vault files** and **where to change or debug** behavior in the repository.
