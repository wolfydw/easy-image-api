#!/usr/bin/env bash

set -euo pipefail

ARCHIVE_URL="https://github.com/wolfydw/easy-image-api/archive/refs/heads/main.zip"
TEMP_DIRECTORY=""
CONFIG_TEMP=""
DEFAULT_MODEL="gpt-image-2.5"
MODEL="${DEFAULT_MODEL}"

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

curl_config_escape() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  printf '%s' "$value"
}

select_model() {
  local model_response="$TEMP_DIRECTORY/models.json"
  local curl_config="$TEMP_DIRECTORY/models-curl.conf"
  local escaped_key
  local model_ids=()
  local model_id
  local choice
  local index

  escaped_key="$(curl_config_escape "$API_KEY")"
  printf 'header = "Authorization: Bearer %s"\n' "$escaped_key" > "$curl_config"
  if curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --connect-timeout 10 \
    --max-time 30 \
    --proto '=https' \
    --tlsv1.2 \
    --config "$curl_config" \
    --output "$model_response" \
    "https://cf.ydw.cool/v1/models" \
    && [[ -s "$model_response" ]]; then
    while IFS= read -r model_id; do
      [[ -n "$model_id" ]] && model_ids+=("$model_id")
    done < <(
      tr '{},' '\n' < "$model_response" \
        | sed -nE 's/.*"id"[[:space:]]*:[[:space:]]*"([^"\\]*)".*/\1/p' \
        | awk '!seen[$0]++'
    )
  fi

  if [[ "${#model_ids[@]}" -gt 0 ]]; then
    printf '%s\n' "可用模型："
    for index in "${!model_ids[@]}"; do
      printf '  %d) %s\n' "$((index + 1))" "${model_ids[$index]}"
    done
    while true; do
      read -r -p "请选择模型编号（直接回车使用 ${DEFAULT_MODEL}）：" choice </dev/tty || {
        printf '\n%s\n' "错误：未收到模型选择，安装已取消。" >&2
        exit 1
      }
      if [[ -z "$choice" ]]; then
        MODEL="${model_ids[0]}"
        for model_id in "${model_ids[@]}"; do
          if [[ "$model_id" == "$DEFAULT_MODEL" ]]; then
            MODEL="$DEFAULT_MODEL"
            break
          fi
        done
        return
      fi
      if [[ "$choice" =~ ^[0-9]+$ ]] && ((choice >= 1 && choice <= ${#model_ids[@]})); then
        MODEL="${model_ids[$((choice - 1))]}"
        return
      fi
      printf '%s\n' "请输入列表中的有效编号。" >&2
    done
  fi

  printf '%s\n' "无法获取可用模型列表，请手动输入模型名。" >&2
  read -r -p "请输入模型名称（直接回车使用 ${DEFAULT_MODEL}）：" choice </dev/tty || {
    printf '\n%s\n' "错误：未收到模型名称，安装已取消。" >&2
    exit 1
  }
  if [[ -n "$choice" ]]; then
    MODEL="$choice"
  fi
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
if [[ -z "$SOURCE_DIRECTORY" || ! -f "$SOURCE_DIRECTORY/SKILL.md" || ! -f "$SOURCE_DIRECTORY/scripts/generate_image.py" || ! -f "$SOURCE_DIRECTORY/scripts/run_generate_image.sh" || ! -f "$SOURCE_DIRECTORY/scripts/run_generate_image.ps1" ]]; then
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
  select_model
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
    printf '  "model": "%s",\n' "$(json_escape "$MODEL")"
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
printf '%s\n' "安装阶段不需要 Python。实际生成或编辑图片时，skill 会探测现有的 Python 3.7+ 环境；不会自动安装 Python。"
printf '%s\n' "请在 Codex 的下一个任务中使用 \$easy-image-api。"
