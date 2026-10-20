# InformationGathering

GitHub Actions 每天采集资讯，读取原文，使用自定义大模型接口整理摘要，再通过邮箱发送五列表格。

**网页采集 → 正文提取 → 普通模型摘要 → 来源/日期/证据/历史排重 → 邮件发送。**

本项目不使用 RSS、Tavily 或模型的联网搜索工具，不需要额外搜索 API 密钥。现有 `LLM_API_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 以及邮件配置继续使用。模型只需要支持普通文本生成。

## 数据来源与筛选

默认配置包含 32 个来源，入口保存在 [config/news_sources.json](config/news_sources.json)：

| 来源 | 获取方式 |
| --- | --- |
| 国家统计局、人力资源和社会保障部、中国政府网 | 直接解析网站新闻列表 |
| 新浪财经、东方财富、每日经济新闻 | 直接解析新闻列表并获取原报道 |
| 广东、河南、四川省及深圳、郑州、成都的政府门户、人社部门、统计局（18 个） | 直接解析各官方网站的文章链接 |
| 比亚迪、立讯精密、蓝思科技、歌尔股份 | 巨潮资讯官方公告接口，按证券代码核对发布主体，提取公告 PDF 正文 |
| 财联社、IT之家、华尔街见闻、36氪 | 通过 NewsNow 获取候选标题和原始链接，再访问原报道页面 |

NewsNow 是候选链接入口，**热榜标题、缓存更新时间和平台简介不会被当作新闻原文或发布日期**。正文由 [Trafilatura](https://github.com/adbar/trafilatura) 提取；缺少原文标题、有效日期或足够正文的文章会跳过并记录失败。公共入口有访问限制或变更时，可修改来源配置或设置自部署的 NewsNow 地址。

程序优先挑选宏观经济、同业产业、指定竞争对手、劳动、人力资源报告、灾害疫情、人力变化等七个方向，尝试兼顾各方向。最终仅保留北京时间今天至前两天内、符合提示词的 1–8 条资讯。同一来源的多篇文章会适当让位给其他有相关资讯的来源。受候选下载与模型调用上限限制，不保证每次覆盖全部网站、地区或方向。

完整用户提示词保留在 [prompts/news_prompt.txt](prompts/news_prompt.txt)，也可用 `NEWS_PROMPT` 替换。标题、日期、媒体与 URL 由采集值确定，模型只生成摘要。每条摘要至少两点，并提供原文逐字引用供程序核对；邮件仍只显示用户要求的五列表格，不显示内部引用字段。单篇正文最多传给模型前 8000 个字符，并标记截取。引用存在性校验不能独立证明摘要对原文的解释完全准确，仍应核对报道。

公告 PDF 使用 pypdf 提取前 12 页，最多下载 8 MiB；扫描件、加密文件或正文不足会跳过。公告日期取官方披露时间，不使用 PDF 创建日期或报告期间。无新公告属于正常情况。新增来源无需额外密钥，保留现有模型与邮件配置即可。

新增网页入口尚未在当前云环境完成在线验证：代理拒绝访问新闻站点。离线验证覆盖配置、来源限制、公告发布主体与 PDF 处理；实际可用性请以 GitHub Actions 的逐来源日志为准。网站改版或首页不提供静态文章链接时，需要调整入口或路径规则。

## GitHub Actions 配置

在仓库 **Settings → Secrets and variables → Actions** 中配置。

### 模型必填项

| 位置 | 名称 | 说明 |
| --- | --- | --- |
| Variables | `LLM_API_BASE_URL` | HTTPS 基础地址，例如 `https://api.facaipeanut.cn/v1`，不带 `/responses` 后缀 |
| Variables | `LLM_MODEL` | 服务商支持的文本模型名称 |
| Secrets | `LLM_API_KEY` | 模型密钥 |

`LLM_API_STYLE` 默认 `responses`，也支持 `chat_completions`。程序只发送普通文本输入，不附加 `tools`、`tool_choice`、搜索参数或引用元数据要求。

此前使用的 `SEARCH_PROVIDER`、`TAVILY_API_KEY`、`NEWS_COLLECTION_MODE`、`LLM_SEARCH_TOOL`、`LLM_EXTRA_BODY`、`LLM_BATCH_TIMEOUT` 已从部署流程移除，仓库里残留的同名变量不会生效，可删除。无需重新创建模型或邮箱凭据。

### 首次运行与预览

1. 打开 **Actions → Daily news digest → Run workflow**，选择 `main`，如需仅测试而不发信，请主动勾选 `dry_run`，新建一次运行。旧记录的 **Re-run jobs** 使用旧提交。
2. 工作流自动安装固定版本依赖，恢复历史，采集并生成摘要。`dry_run` 不需要邮箱配置、不发邮件、不更新正式历史，模型调用仍会消耗额度。
3. 成功生成至少四条资讯后，在运行页面 **Artifacts → news-digest-preview-…** 下载 `report.md` 查看完整表格。
4. 手动运行默认不勾选 `dry_run`，会实际发信并在成功后更新历史；需要仅预览时主动勾选。定时任务按发信模式运行。

默认每天北京时间 **08:00（UTC 00:00）** 执行。可在 [.github/workflows/news.yml](.github/workflows/news.yml) 修改 cron。GitHub 定时可能延迟，公开仓库长期没有活动也可能暂停计划任务。

来源全部不可访问时会失败，不会将访问失败当作“没有新闻”。部分来源或摘要失败时，只要有至少一条合格资讯就会生成报表并发信。没有合格资讯时发送统计通知；若无合格摘要且发生模型处理错误，则报错。401/403/404 模型错误会立即停止，不会自动重试模型或发信请求。

### 邮件配置：先选择发信方式

所有邮件变量都配置在 **GitHub 仓库 → Settings → Secrets and variables → Actions**，不是 Codex 环境设置。名称和值分开填写，值不加引号；当前仅支持使用账号与授权码或应用密码认证的 SMTP 发信。

| 你的发件邮箱 | 推荐方案 | 下方对应章节 |
| --- | --- | --- |
| Gmail / Google Workspace | SMTP + 应用专用密码，需账户允许 | Gmail |
| 其他支持账号密码认证的邮箱 | SMTP + 邮箱授权码或应用密码 | 通用 SMTP |

| 配置位置 | 添加入口 | 存放内容 |
| --- | --- | --- |
| **Variables** 标签 | New repository variable | 普通选项、服务器地址 |
| **Secrets** 标签 | New repository secret | 邮箱授权码或应用专用密码，以及本项目读取的邮箱地址 |

#### Gmail / Google Workspace

| 准备步骤 | 操作 |
| --- | --- |
| 开启两步验证 | 在 [Google 账户安全设置](https://myaccount.google.com/security) 中开启 |
| 创建应用专用密码 | 打开 [应用专用密码](https://myaccount.google.com/apppasswords)，名称可填 `InformationGathering` |
| 保存密码 | 将生成的 16 位应用专用密码去掉分组空格，填入 `GMAIL_SMTP_PASSWORD`；不是 Google 登录密码 |
| 无法创建密码 | 检查账户安全设置；高级保护或 Google Workspace 管理策略可能禁止使用，需联系管理员 |

| 位置 | 名称 | 必填 | 填写值 |
| --- | --- | --- | --- |
| Variables | `GMAIL_SMTP_HOST` | 是 | `smtp.gmail.com` |
| Variables | `GMAIL_SMTP_SECURITY` | 建议显式填写 | `ssl` |
| Variables | `GMAIL_SMTP_PORT` | 建议显式填写 | `465` |
| Secrets | `GMAIL_SMTP_USERNAME` | 是 | 创建应用专用密码的 Google 账户完整邮箱地址 |
| Secrets | `GMAIL_SMTP_PASSWORD` | 是 | 上一步生成的应用专用密码 |
| Secrets | `MAIL_TO` | 是 | 任意收件邮箱；可填自己的 Gmail，多个地址用英文逗号分隔 |
| Secrets | `MAIL_FROM` | 否 | 建议不设置，默认使用 `GMAIL_SMTP_USERNAME`；如已有旧值，请删除或改为当前发件 Gmail 地址 |

| 命名与兼容 | 说明 |
| --- | --- |
| Gmail 配置 | 使用 `GMAIL_SMTP_*`，便于识别；程序和工作流均已支持 |
| 旧配置兼容 | 原有 `SMTP_*` 继续有效；同一配置项同时存在时，非空的 `GMAIL_SMTP_*` 优先 |
| 共用配置 | `MAIL_TO`、`MAIL_FROM`、`MAIL_SUBJECT` 适用于所有邮箱，不加 Gmail 前缀 |
| 切换其他邮箱 | 清除 `GMAIL_SMTP_*` 后再配置通用 `SMTP_*`，避免混用不同邮箱服务的设置 |

#### 通用 SMTP

| 位置 | 名称 | 必填 | 填写值 / 默认值 |
| --- | --- | --- | --- |
| Variables | `SMTP_HOST` | 是 | 邮箱服务商提供的 SMTP 服务器域名，不加 `https://` |
| Variables | `SMTP_SECURITY` | 否 | `ssl`（默认）或 `starttls`，按服务商要求填写 |
| Variables | `SMTP_PORT` | 否 | `ssl` 默认 `465`；`starttls` 默认 `587` |
| Secrets | `SMTP_USERNAME` | 是 | SMTP 登录账号，通常为完整邮箱地址 |
| Secrets | `SMTP_PASSWORD` | 是 | 邮箱服务商提供的 SMTP 授权码或应用密码 |
| Secrets | `MAIL_TO` | 是 | 收件邮箱；多个地址用英文逗号分隔 |
| Secrets | `MAIL_FROM` | 否 | 默认使用 `SMTP_USERNAME`，填写其他地址需服务商允许 |

| 限制 | 说明 |
| --- | --- |
| SMTP 认证要求 | 当前 SMTP 实现使用账号密码认证；请确认服务商允许使用授权码或应用专用密码登录 |

#### 通用选项与运行确认

| 项目 | 配置 / 行为 |
| --- | --- |
| 可选标题 | 在 Variables 配置 `MAIL_SUBJECT`；默认“企业战略与人力资源资讯简报”，自动附加北京时间日期 |
| 正式发信 | Actions → Daily news digest → Run workflow → 选择 `main`，保持 `dry_run` 不勾选（默认） |
| 仅预览 | 主动勾选 `dry_run`；不发邮件、不更新历史，也不会验证邮件凭据 |
| 有合格资讯 | 发送 1–8 条资讯的 HTML 表格正文，原文显示为“查看原文”链接；成功后保存排重历史 |
| 零条合格资讯 | 发送“本次无符合条件的新资讯”统计通知，不更新资讯历史 |
| 实际运行出错 | 全部来源失败、无可用摘要且模型处理出错、邮件发送失败等情况仍会报错 |
| 定时执行 | 每天北京时间 08:00 触发，GitHub 排队可能导致延迟 |

| 常见问题 | 优先检查 |
| --- | --- |
| Gmail SMTP 返回 535 | 是否使用应用专用密码、用户名是否对应生成密码的账户、两步验证是否开启 |
| 工作流成功但未收到邮件 | 检查是否勾选 `dry_run`、发信日志、`MAIL_TO`、垃圾邮件文件夹；服务接受邮件不等于最终投递成功 |


## 采集配置

以下均为可选 Variables，无需为首次运行全部创建：

| 名称 | 默认值 | 用途 |
| --- | --- | --- |
| `NEWS_SOURCES_PATH` | `config/news_sources.json` | 来源配置文件路径 |
| `NEWSNOW_API_URL` | `https://newsnow.busiyi.world/api/s` | 可替换为自部署 NewsNow 的 `/api/s` 地址，不带查询参数 |
| `NEWS_KEYWORDS` | 空 | 用中英文逗号分隔的额外优先关键词，如厂区城市和公司名称 |
| `NEWS_MAX_ARTICLES` | `48` | 最多尝试下载的候选文章数，范围 4–60 |
| `NEWS_MAX_MODEL_CALLS` | `20` | 最多摘要请求数，范围 4–24；收集到八条合格摘要就提前结束 |
| `SOURCE_TIMEOUT` | `15` | 单次来源请求超时秒数，最多 60 |
| `LLM_TIMEOUT` | `300` | 模型请求超时秒数 |
| `LLM_ARTICLE_TIMEOUT` | `90` | 单篇摘要超时，实际取它与 `LLM_TIMEOUT` 的较小值 |
| `NEWS_PROMPT` | 提示词文件 | 自定义选题与事实要求，表格排版由程序负责 |

网站列表的结构如下。自定义网页来源需给出新闻列表 URL、允许的原文域名和文章路径正则；日期、标题和正文仍从原报道提取。继续增加来源时可参考：

```json
{
  "id": "province",
  "name": "某省人民政府",
  "type": "html",
  "url": "https://你的政府站点/新闻列表路径/",
  "allowed_domains": ["你的政府站点"],
  "link_pattern": "/新闻路径/.+\\.html"
}
```

请求只访问配置的 HTTPS 域名，重定向也需在允许域名内；不会把模型密钥或邮件凭据发给新闻网站。来源列表并行读取最多四个，正文按优先顺序逐篇读取并限制总量，不尝试绕过登录、验证码或付费墙。网页改版、动态渲染或反爬可能导致正文提取失败，需要维护具体来源。

## 历史与输出

最近三次正式报表保存在 `.state/history.json`。程序先按 URL 和规范化标题排重，再将历史标题、链接及摘要交给模型跳过同一事件；同一事件换标题或链接的语义排重仍可能漏判。

SMTP 接受邮件后才写入历史，并上传到带运行 ID 的独立 history artifact，下次恢复同一分支最新记录。旧版历史格式及固定名称 artifact 仍可恢复。历史 artifact 保存 90 天；预览 artifact 保存 7 天，仓库或组织策略可能缩短时间。artifact 可见性随仓库权限管理。

邮件已发送但历史写入或上传失败时，下次可能重复报道；邮件服务器接受也不保证最终送达。历史损坏或恢复失败时会停止，避免静默清空排重信息。

## 本地运行

使用 Python 3.12。固定版本依赖保存在 `requirements.txt`。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# 使用 shell 或密钥管理工具设置环境变量；程序不会自动读取 .env。
.venv/bin/python news_digest.py --dry-run --validate-config
.venv/bin/python news_digest.py --dry-run
.venv/bin/python news_digest.py
.venv/bin/python -m unittest discover -s tests -v
```

离线检查和回归测试不访问实际网站、不使用真实密钥、不发送邮件。实际采集需要能访问来源域名、NewsNow 地址和模型 API；Codex 环境的域名限制与 GitHub 托管 runner 的网络条件不同，某一环境的代理拒绝访问不能证明另一环境可用或不可用。

已删除临时的 API/联网搜索测试脚本及其两个手动工作流。`Checks` 保留正文提取、来源限制、摘要证据、邮件 和历史恢复的回归验证；`scripts/restore_history.py` 是正式历史恢复脚本。

来源入口方案参考 [NewsNow](https://github.com/ourongxing/newsnow)（MIT）；正文提取使用 [Trafilatura](https://github.com/adbar/trafilatura)（Apache-2.0）。程序不输出密钥、邮箱密码、完整提示词或模型响应到日志；有效报表可通过预览 artifact 查看。

候选不足排查：日志会显示候选下载预算、原文过期/重复数，以及摘要因无关、重复或事实不足而跳过的原因。默认最多下载 48 篇候选、摘要 20 篇；达到 8 条合格资讯时提前停止摘要。增加预算可能增加运行时间和模型费用；若已在 Variables 显式设置旧值，需要手动修改或删除变量才会使用新默认值。网站返回 403/412 时不会绕过访问限制，不会使用无证据内容补齐报表。

报表数量采用尽力发送规则：目标仍为 4–8 条，但只有 1–3 条合格资讯时也正常发送，不用无关或重复内容补足数量。零条合格资讯会发送“本次无符合条件的新资讯”通知，包含采集和筛选数量，不更新资讯历史；dry_run 仅在日志预览通知，不发邮件。

历史重置时间保存在 `config/history_reset_after.txt`（UTC）。恢复历史时忽略此前生成的历史附件，后续成功发信仍正常建立和恢复历史；此设置不会永久关闭排重。
