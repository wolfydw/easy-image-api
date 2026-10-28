---
name: easy-image-api
description: Generate or edit raster images through the user's own OpenAI-compatible image relay configured in a local JSON file. Use when the user invokes $easy-image-api, asks to use their configured image endpoint, wants to upload one or more source images for image-to-image editing, or wants mask-based local editing through an independent relay instead of Codex's built-in image generation provider.
---

# Easy Image API

Generate or edit images through `scripts/generate_image.py` using a locally stored
endpoint and API key. Keep this provider separate from Codex's built-in image
generator and conversation credentials.

## Configuration

The platform installer copies the skill files and, on first installation, asks
for the API key with visible terminal input so the user can confirm that it was
pasted. It then requests `/v1/models` with that key and lets the user choose a
model before creating `config.json` in the installed skill root. Pressing Enter
on a fresh install uses `gpt-image-2`. During upgrades, the installer marks the
current configured model and uses it when Enter is pressed; selecting another
model updates only the `model` field. If the model list is unavailable, the
installer accepts a manually entered model name or uses the current/default
model when left blank. Installation does not require Python. It sets `endpoint`
to `https://cf.ydw.cool`. Never ask the user to paste an API key into chat or a
command argument.

The source repository's `config.json` remains a placeholder template:

```json
{
  "endpoint": "请填写图片接口地址",
  "api_key": "请填写 API Key",
  "model": "gpt-image-2",
  "size": "auto",
  "quality": "high",
  "output_format": "png"
}
```

Always load configuration from the `config.json` in this skill's installed root
directory, regardless of the current working directory. Do not use another path.
Keep real credentials out of the source repository; the repository copy must
contain placeholders only.

When upgrading an existing installation, read its API key and current model
only to request the model list and provide the default selection. Do not prompt
for the API key again. Update only the `model` field when the user selects a
different model, and preserve every other configuration field. Never replace an
existing user configuration with the repository's placeholder configuration.

Treat `endpoint` as either the relay's base endpoint or a complete generations or
edits endpoint. Resolve generation requests to `/v1/images/generations` and edit
requests to `/v1/images/edits`, preserving any existing path prefix. When a relay
uses a different edit URL or multipart image field, add these optional values:

```json
{
  "edit_endpoint": "https://relay.example.com/custom/images/edits",
  "edit_image_field": "image"
}
```

Default `edit_image_field` to `image[]`, matching the OpenAI-compatible behavior
used by GPT Image Playground. Read `api_key` only from the configuration file and
never print it. Never ask the user to paste an API key into chat or a command
argument.

Command options override configured request defaults without changing where the
configuration file is stored.

## Workflow

1. Convert the request into a concise visual prompt. Preserve exact requested
   text, composition, edit instructions, and exclusions. For mask editing,
   describe the complete desired result and explicitly request that content
   outside the masked region remain unchanged.
2. Select the operation:
   - Use generation mode when the request has no source image.
   - Use edit mode when the user provides one or more source images.
   - Add `--mask` only when the user provides a prepared transparent PNG mask.
3. Choose a new output path under the current workspace. For projectless tasks,
   prefer `outputs/easy-image-api/<descriptive-name>.<format>`.
4. Run the platform launcher bundled with the skill. It first checks for an existing Python 3.7+ interpreter and uses the first one it finds; it does not install Python or require a fixed 3.9 version.

Generation:

```text
macOS/Linux:

    <skill-dir>/scripts/run_generate_image.sh --prompt "<prompt>" --out "<absolute-output-path>"

Windows PowerShell:

    & <skill-dir>/scripts/run_generate_image.ps1 --prompt "<prompt>" --out "<absolute-output-path>"
```

Source-image editing:

```text
macOS/Linux:

    <skill-dir>/scripts/run_generate_image.sh --image "<absolute-source-path>" --prompt "<prompt>" --out "<absolute-output-path>"

Windows PowerShell:

    & <skill-dir>/scripts/run_generate_image.ps1 --image "<absolute-source-path>" --prompt "<prompt>" --out "<absolute-output-path>"
```

Repeat `--image` for multiple reference images. The first image is the primary
edit target when a mask is present.

Mask editing:

```text
macOS/Linux:

    <skill-dir>/scripts/run_generate_image.sh --image "<absolute-source-png>" --mask "<absolute-mask-png>" --prompt "<prompt>" --out "<absolute-output-path>"

Windows PowerShell:

    & <skill-dir>/scripts/run_generate_image.ps1 --image "<absolute-source-png>" --mask "<absolute-mask-png>" --prompt "<prompt>" --out "<absolute-output-path>"
```

The launchers check Python candidates in this order: Windows `py -3`, `python3`,
then `python`; macOS/Linux `python3`, then `python`. A candidate is accepted only
when it is Python 3.7 or newer. The script uses only the Python standard library;
no `pip install` or third-party package is required. If no candidate is available,
report that Python 3 must be installed and stop rather than silently installing it.

5. As soon as the request succeeds, render every saved image in the response with
   an absolute Markdown image path. Do not inspect, review, or regenerate it before
   returning it; prioritize delivery speed and let the user judge the result.
   Report the operation, model, size, quality, and saved path printed by the script.

Use `--n` only for variants of one prompt. For distinct images, run one request
per prompt. Never overwrite an existing output unless the user explicitly requests
replacement; choose a new filename or pass `--force` only with explicit intent.
The script always rejects an output path that matches a source image or mask, even
with `--force`. Normalize the output filename to the requested format; accept both
`.jpg` and `.jpeg` for JPEG. For multiple variants, append `-1`, `-2`, and so on.
Reject a response whose detected image format differs from the requested format,
and do not write a partial batch.

## Edit Constraints

Accept up to 16 PNG, JPEG, or WebP input images. Upload them as repeated multipart
fields in their CLI order. Treat a mask as applying only to the first input image.

Require the mask and first input image to be PNG files with identical dimensions.
Require the mask to have an alpha channel and at least one transparent or
semi-transparent pixel. Interpret transparent or semi-transparent areas as editable
and fully opaque areas as preserved. The script accepts non-interlaced 8-bit or
16-bit grayscale-alpha or RGBA masks. A mask guides the model but does not guarantee
pixel-exact containment.

Do not claim an in-app mask painting UI, automatic subject selection, conversational
continuity, streaming previews, or native transparency control. Ask the user for a
prepared mask when local editing requires one and no mask was supplied.

## Supported Responses

Accept common OpenAI-compatible responses containing `data[].b64_json` or
`data[].url`, plus common `base64`, `image_base64`, `image_url`, `images[]`, and
image-generation `output[]` variants.

Use `--dry-run` to inspect the operation, resolved request URL, text fields, input
file paths/formats/sizes, and output paths without using the API key, embedding
image bytes, or making a network request.
