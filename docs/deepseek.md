# DeepSeek backend

This fork uses DeepSeek's OpenAI-compatible Chat Completions endpoint for all
text and tool-calling routes. The OpenAI Python package remains the transport
SDK; requests are sent to `https://api.deepseek.com`, not to OpenAI.

## Configuration

Set the credential in the process environment:

```powershell
$env:DEEPSEEK_API_KEY="your-key"
```

Do not put the real value in `.env.example`, source control, logs, or command
arguments. The packaged `src/memslides/memslides.yaml` reads this environment
variable directly.

Default routes are:

| Route | Default model |
| --- | --- |
| research, design, modify, review, long-context | `deepseek-v4-pro` |
| fast and balanced memory tasks | `deepseek-v4-flash` |

Every model can still be overridden independently, for example:

```powershell
$env:MEMSLIDES_DESIGN_MODEL="deepseek-v4-flash"
$env:MEMSLIDES_DESIGN_BASE_URL="https://api.deepseek.com"
```

The configuration disables DeepSeek thinking mode. MemSlides runs a multi-turn
tool loop, while DeepSeek thinking-mode tool calls require
`reasoning_content` to be replayed on subsequent requests. Non-thinking mode
keeps the current MemSlides protocol compatible and still supports tool calls
and JSON output.

## Important limitations

- The configured DeepSeek V4 endpoint is text-only. The `is_multimodal` flags
  are therefore false. Template/image-analysis features that require a vision
  model need a separate multimodal provider configured explicitly.
- DeepSeek does not provide the embedding endpoint used by MemSlides memory.
  This fork defaults memory embeddings to local `BAAI/bge-m3`. Install the
  research extras before enabling memory-heavy workflows:

  ```bash
  pip install -e ".[research]"
  ```

- The financial integration does not ask the LLM to recreate audited charts or
  tables. It passes deterministic SVG assets into the design layer, so using a
  text-only design model does not alter audited values.

