#!/usr/bin/env bash

set -euo pipefail

ARCHIVE_URL="https://github.com/wolfydw/easy-image-api/archive/refs/heads/main.zip"
TEMP_DIRECTORY=""
CONFIG_TEMP=""

cleanup() {
  if [[ -n "$CONFIG_TEMP" && -f "$CONFIG_TEMP" ]]; then
    rm -f -- "$CONFIG_TEMP"
  fi
  if [[ -n "$TEMP_DIRECTORY" && -d "$TEMP_DIRECTORY" ]]; then
    rm -rf -- "$TEMP_DIRECTORY"
  fi
}

trap cleanup EXIT HUP INT TERM

json_escape() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  value=${value//$'\t'/\\t}
  printf '%s' "$value"
}

if ! command -v curl >/dev/null 2>&1; then
  printf '%s\n' "错误：安装需要 curl。" >&2
  exit 1
fi
if ! command -v unzip >/dev/null 2>&1; then
  printf '%s\n' "错误：安装需要 unzip。" >&2
  exit 1
fi

umask 077
TEMP_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/easy-image-api-installer.XXXXXX")"
ARCHIVE_PATH="$TEMP_DIRECTORY/easy-image-api.zip"
EXTRACT_PATH="$TEMP_DIRECTORY/extracted"
DESTINATION="${CODEX_HOME:-$HOME/.codex}/skills/easy-image-api"
mkdir -p "$EXTRACT_PATH"

curl \
  --fail \
  --silent \
  --show-error \
  --location \
  --proto '=https' \
  --tlsv1.2 \
  --output "$ARCHIVE_PATH" \
  "$ARCHIVE_URL"

unzip -q "$ARCHIVE_PATH" -d "$EXTRACT_PATH"
SOURCE_DIRECTORY="$(find "$EXTRACT_PATH" -mindepth 1 -maxdepth 1 -type d -print -quit)"
if [[ -z "$SOURCE_DIRECTORY" || ! -f "$SOURCE_DIRECTORY/SKILL.md" || ! -f "$SOURCE_DIRECTORY/scripts/generate_image.py" ]]; then
  printf '%s\n' "错误：安装包缺少必要的 skill 文件。" >&2
  exit 1
fi

CONFIG_PATH="$DESTINATION/config.json"
HAS_EXISTING_CONFIG=0
if [[ -e "$CONFIG_PATH" || -L "$CONFIG_PATH" ]]; then
  HAS_EXISTING_CONFIG=1
fi
API_KEY=""
if [[ "$HAS_EXISTING_CONFIG" -eq 0 ]]; then
  if [[ ! -r /dev/tty ]]; then
    printf '%s\n' "错误：首次安装需要交互式终端输入 API Key。" >&2
    exit 1
  fi
  while [[ -z "$API_KEY" ]]; do
    if ! read -r -p "请输入生图 API Key：" API_KEY </dev/tty; then
      printf '\n%s\n' "错误：未收到 API Key，安装已取消。" >&2
      exit 1
    fi
    if [[ -z "$API_KEY" ]]; then
      printf '%s\n' "API Key 不能为空，请重新输入。" >&2
    fi
  done
fi

DESTINATION_EXISTS=0
if [[ -d "$DESTINATION" ]]; then
  DESTINATION_EXISTS=1
else
  mkdir -p "$DESTINATION"
fi

for managed in SKILL.md agents assets references scripts; do
  if [[ -e "$SOURCE_DIRECTORY/$managed" ]]; then
    cp -R "$SOURCE_DIRECTORY/$managed" "$DESTINATION/"
  fi
done

if [[ "$HAS_EXISTING_CONFIG" -eq 0 ]]; then
  ESCAPED_API_KEY="$(json_escape "$API_KEY")"
  CONFIG_TEMP="$(mktemp "$DESTINATION/.config.json.XXXXXX")"
  {
    printf '%s\n' '{'
    printf '  "endpoint": "https://cf.ydw.cool",\n'
    printf '  "api_key": "%s",\n' "$ESCAPED_API_KEY"
    printf '%s\n' '  "model": "gpt-image-2.5",'
    printf '%s\n' '  "size": "auto",'
    printf '%s\n' '  "quality": "high",'
    printf '%s\n' '  "output_format": "png"'
    printf '%s\n' '}'
  } > "$CONFIG_TEMP"
  if ln "$CONFIG_TEMP" "$CONFIG_PATH" 2>/dev/null; then
    :
  fi
  rm -f -- "$CONFIG_TEMP"
  CONFIG_TEMP=""
fi

if [[ "$HAS_EXISTING_CONFIG" -eq 1 ]]; then
  printf '%s\n' "easy-image-api skill 已升级：$DESTINATION"
  printf '%s\n' "已保留原有 config.json，未读取、覆盖或修改。"
  printf '%s\n' "配置文件位置：$DESTINATION/config.json"
else
  printf '%s\n' "easy-image-api skill 已安装：$DESTINATION"
  printf '%s\n' "配置文件已生成：$DESTINATION/config.json"
fi
printf '%s\n' "安装阶段不需要 Python。实际生成或编辑图片时需要 Python 3.9 或更高版本。"
printf '%s\n' "请在 Codex 的下一个任务中使用 \$easy-image-api。"
