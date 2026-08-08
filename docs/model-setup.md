# Model and Docker setup

The application image is independent from the model runtime. Each company stores
its own routing mode and local model name. The deployment chooses where Ollama
lives; the dashboard chooses which installed model a company uses.

## Option 1: bundled Ollama and default model

This is the simplest complete installation. It downloads the customized
DeepSeek-R1 8B Q4_K_M model, approximately 5.2 GB.

CPU:

```powershell
.\scripts\up.ps1 -Mode Bundled -Build
```

NVIDIA GPU on Windows/WSL2 or Linux:

```powershell
.\scripts\up.ps1 -Mode Bundled -Gpu -Build
```

Follow first-run model preparation:

```powershell
docker compose -f compose.yaml -f compose.local.yaml logs -f ollama-init
```

## Option 2: bundled Ollama without downloading our model

Create `.env.local` from `.env.example` and set:

```env
INSTALL_DEFAULT_MODEL=false
```

Then start bundled Ollama:

```powershell
.\scripts\up.ps1 -Mode Bundled -Gpu -Build
```

Install any model later and select it from **Model settings**:

```powershell
docker compose -f compose.yaml -f compose.local.yaml exec ollama ollama pull llama3.1:8b
```

## Option 3: use models already installed on the host

First confirm the host installation:

```powershell
ollama list
```

Ollama must accept connections from Docker. Start it with
`OLLAMA_HOST=0.0.0.0:11434` according to the host operating system, then set
this in `.env.local`:

```env
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
```

Start only the application container:

```powershell
.\scripts\up.ps1 -Mode Existing -Build
```

Open the dashboard, create or pause a company, select **Model settings**, press
**Refresh local models**, and select any installed name such as `llama3.1:8b`,
`qwen3:8b`, or a custom Ollama model. Do not guess the tag: use the exact value
shown by `ollama list`.

For Ollama on another machine, set `OLLAMA_BASE_URL` to its reachable private
network address. Do not expose an unauthenticated Ollama port to the public
internet.

## Option 4: OpenAI cloud only

Create `.env.local` and supply the key locally:

```env
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-5.4-mini
COMPUTER_USE_MODEL=gpt-5.6
COMPUTER_USE_MAX_STEPS=12
```

Start without Ollama:

```powershell
.\scripts\up.ps1 -Mode Cloud -Build
```

In **Model settings**, choose **Cloud only**. No local model is contacted and no
model is downloaded. The API key is runtime configuration and is not copied into
the Docker image.

## Option 5: hybrid routing

Provide both a reachable Ollama server and `OPENAI_API_KEY`. Start either bundled
or existing-local mode, then choose **Hybrid** in **Model settings**. The current
router uses OpenAI for CEO, Development, and Research. Research also receives the
hosted web-search tool. The selected local model handles Product, Platform,
Operations, QA, Growth, and supporting analysis.

## Working with another developer

Commit source and Compose files, but never commit `.env`, `.env.local`, company
data, or model volumes. Each developer may use a different local model while the
company setting in their own data volume remains local to that installation.

Recommended first commands after cloning:

```powershell
Copy-Item .env.example .env.local
.\scripts\up.ps1 -Mode Existing -Build
```

If that developer has no Ollama model, use `-Mode Bundled`. If they only want to
work on UI/API code, use `-Mode Cloud` and leave the company stopped unless a
valid OpenAI key is configured.

## Diagnostics

```powershell
docker compose ps -a
docker compose logs --tail 100 app
Invoke-RestMethod http://127.0.0.1:8421/health
Invoke-RestMethod http://127.0.0.1:8421/api/local-model/models
```

Stop containers while preserving data and downloaded models:

```powershell
.\scripts\down.ps1
```
