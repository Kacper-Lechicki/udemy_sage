# udemy-sage

Self-hosted CLI tool that downloads subtitles from a purchased Udemy course and uses AI to generate detailed Markdown notes — directly into your Obsidian vault.

**Documentation:** [In-depth guide](docs/GUIDE.md) (architecture, data flow, modules, and where to look in the code).

## Install

```bash
git clone <repo-url> && cd udemy-sage
pipx install ".[all]"
```

Install with only the provider you need:

```bash
pipx install ".[openai]"
pipx install ".[anthropic]"
pipx install ".[gemini]"
```

Ollama requires no extra dependencies.

To reinstall after updating the source:

```bash
pipx install --force ".[all]"
```

### Prerequisites

- A browser with an active Udemy session (for cookie-based authentication)

## Usage

```bash
udemy-sage
```

On first run, an interactive wizard configures:

| Setting    | Description                                                      |
| ---------- | ---------------------------------------------------------------- |
| Provider   | `openai`, `anthropic`, `gemini`, `openrouter`, `ollama`          |
| Model      | Pre-filled with recommended default                              |
| API key    | Stored in `~/.udemy-sage/config.json` (chmod 600)                |
| Vault path | Path to your Obsidian vault                                      |
| Browser    | Browser to extract Udemy cookies from                            |
| Ollama URL | Shown when provider is Ollama (default `http://localhost:11434`) |

Subsequent runs only ask for the course URL.

Run `udemy-sage --help` for flags and examples.

To change settings later:

```bash
# Re-run the full wizard
udemy-sage --reconfigure

# Change a single setting
udemy-sage --reconfig=provider
udemy-sage --reconfig=cookies_browser
udemy-sage --reconfig=model
udemy-sage --reconfig=api_key
udemy-sage --reconfig=vault_path
udemy-sage --reconfig=ollama_base_url

# Override Ollama URL for a single run (e.g. remote host)
udemy-sage --ollama-base-url http://192.168.1.10:11434
```

The wizard rejects incomplete settings (for example an empty model name or a missing API key when not using Ollama). The vault path must already exist before a course fetch starts.

Note generation runs **up to five lessons in parallel** (one shared API client per run) to speed up large courses while limiting rate-limit risk.

## Output

Notes land in `<vault>/resources/udemy/<course-slug>/<section>/<lesson>.md`. The `<course-slug>` folder is derived from the **Udemy course URL** (`/course/<slug>/`), not the course title, so two different courses with the same title get separate directories.

```
resources/udemy/complete_python_bootcamp/
├── complete_python_bootcamp.md          # Dataview index
├── 01_introduction/
│   ├── 001_welcome.md
│   └── 002_setup.md
└── 02_functions/
    ├── 003_defining_functions.md
    └── 004_lambda_expressions.md
```

Each note contains:

- **Summary** — concise lesson overview
- **Key concepts** — main topics as bullet points
- **Code examples** — annotated code from the lesson
- **Insights** — deeper observations and practical tips
- **Questions to reflect on** — self-assessment prompts

## Recommended models

| Provider   | Model                       | Cost per ~1h course |
| ---------- | --------------------------- | ------------------- |
| OpenAI     | `gpt-4o-mini`               | ~$0.02              |
| Anthropic  | `claude-3-5-haiku-20241022` | ~$0.03              |
| Gemini     | `gemini-2.0-flash`          | ~$0.01              |
| OpenRouter | `openai/gpt-4o-mini`        | variable            |
| Ollama     | `llama3.2`                  | $0.00               |

## Architecture

```
URL → cookies (browser-cookie3) → Udemy API → VTT parser → ai_client → renderer → vault
```

Six modules with clear boundaries:

| Module         | Responsibility                                        |
| -------------- | ----------------------------------------------------- |
| `config.py`    | Read/write `~/.udemy-sage/config.json`, error logging |
| `fetcher.py`   | Udemy API client, cookie extraction, VTT download     |
| `parser.py`    | Data models, VTT parsing, slugify                     |
| `ai_client.py` | Multi-provider AI with retry + backoff                |
| `renderer.py`  | Markdown generation, skip-if-exists, Dataview index   |
| `cli.py`       | Interactive wizard, progress bar, cost summary        |

## Security

- Config directory `~/.udemy-sage` uses `chmod 700`; `config.json` and `errors.log` use `chmod 600` (on Unix). On Windows, permission tightening is applied best-effort and may be a no-op.
- API keys are never logged; keep `config.json` out of version control (`.env` is gitignored if you use one).
- Subtitle files are downloaded only over **HTTPS** from **Udemy** hostnames (`*.udemy.com`, `*.udemycdn.com`).
- Temp VTT files are held in a `TemporaryDirectory` and removed when the fetch step finishes.
- `errors.log` contains only lesson identifiers, never transcripts or keys.
- Ollama: by default traffic stays on your machine; a custom base URL sends requests only to the host you configure.

## Legal disclaimer

**Personal use only.** You must own the Udemy course. Accessing course content via the Udemy API may violate Udemy's Terms of Service — the user assumes all responsibility. Generated notes are derivative works of the original course content.

## License

MIT
