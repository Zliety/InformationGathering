# InformationGathering

GitHub Actions 定时调用大模型的联网搜索能力，按照企业战略与人力资源情报提示词收集近三天新闻，将结果通过邮箱发送。无需 RSS，仅使用 Python 3.12 标准库。

你提供的完整提示词已保存在 [prompts/news_prompt.txt](prompts/news_prompt.txt)，覆盖七个维度、权威来源、4–8 条资讯、原文标题和原始链接。邮件正文为五列表格，摘要分点换行，没有额外正文；同时提供纯文本版本。

## 1. 配置模型接口

在 GitHub 仓库 **Settings → Secrets and variables → Actions** 中配置。

**Secrets：**

| 名称 | 用途 |
| --- | --- |
| `LLM_API_KEY` | 大模型接口密钥 |
| `SMTP_USERNAME` | SMTP 登录账号，通常为邮箱地址 |
| `SMTP_PASSWORD` | 邮箱 SMTP 授权码或应用专用密码 |
| `MAIL_TO` | 收件邮箱，多个地址用英文逗号分隔 |
| `MAIL_FROM` | 可选，发件邮箱；默认使用 `SMTP_USERNAME`，需符合服务商要求 |

**Variables：**

| 名称 | 默认值 / 说明 |
| --- | --- |
| `LLM_API_BASE_URL` | 必填，例如 `https://api.openai.com/v1`；不要填写完整的 `/responses` 或 `/chat/completions` 路径 |
| `LLM_MODEL` | 必填，必须是服务商支持联网搜索的模型 |
| `LLM_API_STYLE` | 默认 `responses`；也可设为 `chat_completions` |
| `LLM_SEARCH_TOOL` | Responses 模式默认 `web_search`；部分兼容服务使用 `web_search_preview` |
| `LLM_EXTRA_BODY` | 可选，服务商的额外 JSON 参数；Chat Completions 模式必填联网搜索参数 |
| `LLM_TIMEOUT` | 默认 `300` 秒，单次模型请求超时 |
| `NEWS_PROMPT` | 可选，完整覆盖默认提示词；不设置则读取仓库中的提示词文件 |
| `SMTP_HOST` | 必填，邮箱服务商的 SMTP 主机 |
| `SMTP_PORT` | 默认 SSL 为 `465`，STARTTLS 为 `587` |
| `SMTP_SECURITY` | 默认 `ssl`；也可设为 `starttls` |
| `MAIL_SUBJECT` | 默认 `企业战略与人力资源资讯简报`，自动附加北京时间日期 |

### Responses 接口（推荐）

程序请求 `LLM_API_BASE_URL + /responses`，明确启用 `web_search` 工具并要求使用工具，而不是仅在提示词里要求“联网”。例如配置：

```text
LLM_API_BASE_URL=https://api.openai.com/v1
LLM_API_STYLE=responses
LLM_MODEL=gpt-4.1
```

`gpt-4.1` 只是配置示例；以服务商当前支持的模型、联网工具和账户权限为准。兼容服务须支持 Responses 联网工具、`tool_choice: required`、`include: ["web_search_call.action.sources"]`，并返回完成的搜索调用及来源元数据。

### Chat Completions 兼容接口

程序请求 `LLM_API_BASE_URL + /chat/completions`。不同服务商的联网参数不统一，须根据其文档设置 `LLM_EXTRA_BODY`。例如，支持 OpenAI Chat Completions 搜索模型的接口可使用：

```text
LLM_API_STYLE=chat_completions
LLM_MODEL=gpt-4o-search-preview
LLM_EXTRA_BODY={"web_search_options":{}}
```

支持 `enable_search` 的其他服务商可能需要 `{"enable_search":true}` 及其他参数，请以其文档为准。接口须返回 `annotations`、`citations`、`search_results` 或 `search_info` 中的真实来源 URL。程序会核对每条新闻的 URL 是否出现在这些服务商返回的元数据中；不支持联网、没有来源元数据、响应截断或表格格式错误都会失败，不会发送缺少证据的结果。**普通对话模型仅靠提示词无法获得实时搜索能力。**

`LLM_EXTRA_BODY` 不允许覆盖模型、消息、流式开关或 Responses 工具等核心字段。模型输出会经过格式、来源 URL 和重复检查，但这些检查不能独立证明原文标题、报道日期或每一项摘要事实准确，仍需核对原始报道。

## 2. 配置定时与首次运行

默认每天 **北京时间 08:00（UTC 00:00）** 运行。可以修改 [.github/workflows/news.yml](.github/workflows/news.yml) 的 cron。GitHub 调度可能延迟，仅在默认分支运行；公开仓库长期无活动时可能停用定时任务，请确认工作流已启用。

将代码推送到仓库后，打开 **Actions → Daily news digest → Run workflow**。首次建议勾选 `dry_run`：真实联网搜索并校验结果，但不发邮件、不更新正式历史，也不会在日志中公开报表。取消勾选会实际发送邮件。调用模型和联网搜索可能产生费用，SMTP 服务商须允许 GitHub 托管 runner 发信。

例如 QQ 邮箱通常使用 `smtp.qq.com`、`465`、`ssl`，密码应为启用 SMTP 后生成的授权码；其他邮箱按服务商文档配置。不要在聊天或源码中填写密钥和授权码。

## 3. 最近三次历史排重

每次把最近三次正式报表的全文、标题、URL 提供给模型，要求跳过重复事件。程序还会拒绝与历史相同的 URL 或规范化标题，以及同一报表内部的重复条目。不同标题/链接报道同一事件的语义排重由模型执行，不能保证完全识别。

邮件被 SMTP 服务器接受后，本地写入 `.state/history.json`，仅保留最近三份报表。Actions 将其存储到 `information-gathering-history` artifact；下次运行通过 GitHub CLI 恢复同一分支最近的 artifact，不依赖临时 runner 磁盘或不可更新的缓存。需要 `actions: read`，工作流已配置。定时和手动任务按分支串行执行。

artifact 保存期限为 90 天，组织策略可能缩短期限。首次运行或历史 artifact 全部过期/删除后无法恢复历史，从空历史开始，日志会提示；恢复出错或历史损坏时直接停止，防止静默丢失排重。artifact 包含报表内容，请按仓库可见性管理访问。

SMTP 接受邮件不保证最终投递。若已发信后历史写入或 artifact 上传失败，本次邮件无法撤回，下次可能重报；应先检查运行日志。程序不自动重试模型或发信请求，避免额外费用和重复邮件。手动重新运行成功过的发信任务会产生一份新报表，并非重新发送旧报表。

## 4. 本地开发

```bash
# 使用 shell 或密钥管理工具设置环境变量，不会自动读取 .env。
python3 news_digest.py --validate-config  # 离线检查配置和历史
python3 news_digest.py --dry-run          # 无需 SMTP 配置，真实搜索但不发邮件
python3 news_digest.py                    # 实际发信，并更新本地历史
python3 -m unittest discover -s tests -v  # 不联网，不使用真实密钥，不发邮件
```

本地历史默认在 `.state/history.json`，可用 `HISTORY_PATH` 覆盖。模型请求携带北京时间和今天至前两天的日期范围。格式要求不足四条符合条件的资讯时返回失败，不凑数、不发空邮件。

日志只记录阶段、条目数和错误类别，不输出接口密钥、邮箱密码、收件地址、完整提示词、历史报表或模型响应。模型会收到提示词及最近三份报表，请选择你信任的服务商。
