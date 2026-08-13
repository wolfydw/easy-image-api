# Easy Image API

## 项目介绍

Easy Image API 是一个 Codex skill，让 Codex 通过你自己的 OpenAI-compatible 图片接口生成和编辑图片。

它主要解决这些问题：

- 不再受限于 Codex 内置的图片服务，可以使用自己的中转站、API Key 和模型。
- 用自然语言完成文生图、原图编辑、多图参考和透明遮罩局部编辑，无需手动拼接 API 请求。
- 自动兼容常见的 Base64 和图片 URL 响应，并校验输出格式，减少不同中转站之间的适配工作。
- API Key 只保存在本机配置文件中，不需要发送到 Codex 对话。

项目支持 PNG、JPEG、WebP 输入与输出，最多可同时上传 16 张参考图。运行脚本只依赖 Python 3.9+ 标准库，无需安装第三方 Python 包。

## ❤️ 赞助商

<table>
<tr>
<td width="210" align="center"><a href="https://ydw.cool"><img src="assets/partners/logos/yunduanwang.png" alt="云端网" width="180"></a></td>
<td><h3>云端网 · Codex 全模型中转站</h3><p>感谢 <a href="https://ydw.cool"><strong>云端网</strong></a> 赞助本项目！云端网为开发、编程与 Agent 工作流提供便捷、平价、稳定的全模型 API 接入服务，适合需要长期、稳定调用模型的个人开发者和团队。</p><p><strong>稳定运行，从未断线。</strong></p><p><strong><a href="https://ydw.cool">立即访问云端网 →</a></strong></p></td>
</tr>
</table>

## 在 Codex 中使用

安装并完成首次配置后，在 Codex 中直接调用 `$easy-image-api`。

生成图片：

```text
$easy-image-api 生成一张白色背景的极简产品图，主体是一台银色咖啡机
```

编辑图片：

```text
$easy-image-api 把这张图片中的红色杯子改成蓝色，其他内容保持不变
```

使用多张参考图：

```text
$easy-image-api 保留第一张图的构图，采用第二张图的配色和材质风格
```

使用透明 PNG 遮罩进行局部编辑：

```text
$easy-image-api 只把遮罩区域内的背景改成浅灰色，其他区域保持不变
```

发送编辑请求时，将原图、参考图或遮罩与提示词一起提供给 Codex 即可。生成结果会保存到当前工作区并直接展示。

<details>
<summary><strong>项目结构</strong></summary>

```text
├── SKILL.md                    # Codex skill 定义与执行流程
├── config.json                # 接口配置模板（仅包含中文占位符）
├── scripts/
│   └── generate_image.py      # 图片生成与编辑脚本
├── tests/
│   └── test_generate_image.py # 单元测试
├── README.md                  # GitHub 项目说明
└── AGENTS.md                  # 仓库开发与部署规则
```

</details>

## 使用 Codex 一键安装

在 Codex 中新建一个任务，粘贴下面这句话并发送：

```text
请安装这个 skill：https://github.com/wolfydw/easy-image-api。安装后提示我在本地编辑器中填写 skill 目录内 config.json 的接口地址和 API Key，不要让我把 API Key 粘贴到对话中。如果是升级，必须保留已有的 config.json，不得读取、覆盖、删除或修改，只更新其他文件。
```

Codex 会从 GitHub 安装 `easy-image-api`，带中文占位符的配置文件会随 skill 一起安装到 `~/.codex/skills/easy-image-api/config.json`。请在本地编辑器中填写自己的接口地址和 API Key；图片模型默认使用 `gpt-image-2`。升级时，Codex 必须保留已有的 `config.json`，只更新其他文件。安装完成后，在下一个任务中即可使用 `$easy-image-api`。

## License

本项目尚未添加开源许可证。
