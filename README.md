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
├── install.sh                  # macOS/Linux 一键安装入口
├── install.ps1                 # Windows PowerShell 一键安装入口
├── SKILL.md                    # Codex skill 定义与执行流程
├── config.json                # 接口配置模板（仅包含中文占位符）
├── scripts/
│   ├── generate_image.py      # 图片生成与编辑脚本
│   └── install_skill.py       # 跨平台安装逻辑
├── tests/
│   ├── test_generate_image.py # 图片生成与编辑测试
│   └── test_install_skill.py  # 安装与升级测试
├── README.md                  # GitHub 项目说明
└── AGENTS.md                  # 仓库开发与部署规则
```

</details>

## 一键安装

安装前请确认电脑已安装 Python 3.9 或更高版本，然后根据系统在终端中运行对应命令。

macOS / Linux：

```bash
curl -fsSL https://raw.githubusercontent.com/wolfydw/easy-image-api/main/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/wolfydw/easy-image-api/main/install.ps1 | iex
```

首次安装时，安装器会在终端中隐藏输入内容并询问生图 API Key，不会把密钥显示在屏幕上或放入命令参数。安装器随后自动生成本机 `config.json`，将接口地址写为 `https://cf.ydw.cool`，图片模型默认为 `gpt-image-2.5`，无需手工编辑配置。

再次运行同一条命令即可升级。升级时，安装器会保留已有的 `config.json`，不会读取、覆盖、删除或修改它，也不会再次询问 API Key；只有其他 skill 文件会更新。仓库中的 `config.json` 始终只包含占位符，不含真实接口信息或密钥。

安装完成后，请在 Codex 的下一个任务中使用 `$easy-image-api`。

## License

本项目尚未添加开源许可证。
