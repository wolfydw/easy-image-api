---
name: easy-image-api
description: Generate or edit raster images through the user's own OpenAI-compatible image relay configured in a local JSON file. Use when the user invokes $easy-image-api, asks to use their configured image endpoint, wants to upload one or more source images for image-to-image editing, or wants mask-based local editing through an independent relay instead of Codex's built-in image generation provider.
---

# Easy Image API

Generate or edit images through `scripts/generate_image.py` using a locally stored
endpoint and API key. Keep this provider separate from Codex's built-in image
generator and conversation credentials.

## Configuration

Use the platform installer to configure the skill. On first installation, it
prompts for the API key with hidden terminal input and creates `config.json` in
the installed skill root. It automatically sets `endpoint` to
`https://cf.ydw.cool` and keeps `gpt-image-2.5` as the default model, so do not
ask the user to edit the endpoint or API key manually during initial setup. Do
not ask the user to paste an API key into chat or a command argument.

The source repository's `config.json` remains a placeholder template:

```json
{
  "endpoint": "请填写图片接口地址",
  "api_key": "请填写 API Key",
  "model": "gpt-image-2.5",
  "size": "auto",
  "quality": "high",
  "output_format": "png"
}
```

Always load configuration from the `config.json` in this skill's installed root
directory, regardless of the current working directory. Do not use another path.
Keep real credentials out of the source repository; the repository copy must
contain placeholders only.

When upgrading an existing installation, preserve its `config.json` without
reading, overwriting, deleting, or modifying it, and do not prompt for the API
key again. Update only the other skill files. Never replace an existing user
configuration with the repository's placeholder configuration.

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
4. Run the bundled script with an available Python 3 interpreter.

Generation:

```text
python3 <skill-dir>/scripts/generate_image.py --prompt "<prompt>" --out "<absolute-output-path>"
```

Source-image editing:

```text
python3 <skill-dir>/scripts/generate_image.py --image "<absolute-source-path>" --prompt "<prompt>" --out "<absolute-output-path>"
```

Repeat `--image` for multiple reference images. The first image is the primary
edit target when a mask is present.

Mask editing:

```text
python3 <skill-dir>/scripts/generate_image.py --image "<absolute-source-png>" --mask "<absolute-mask-png>" --prompt "<prompt>" --out "<absolute-output-path>"
```

On Windows, prefer `py -3`; otherwise try `python3`, then `python`, and require
Python 3.9 or newer. The script uses only the Python standard library.

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
