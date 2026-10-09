# 漫游 · AI 旅行规划助手

中文基础演示版，React + TypeScript + Vite 前端，FastAPI + Python 后端。目的地、出发日期、旅行天数、人数、预算、币种与多选兴趣经前后端校验后，生成按天排列的活动卡片，展示时间、模拟地点、活动说明及预计费用。支持加载、错误提示、演示 15 秒／真实 130 秒请求超时及重新生成，适配桌面与手机。

**模式**：demo 使用演示生成器，无需密钥；live 已接入千问＋博查 Agent，通过搜索证据生成真实模式行程，不包含地图或预订。demo 每次从兴趣模板随机编排，地点明确标注模拟；live 失败只返回错误，绝不回退演示。真实费用和营业时间可能未知，来源可追溯不代表准确性保证。

## 环境与安装

- Node.js 20+（推荐 24 LTS）及 npm；Python 3.10+。
- 本次环境默认 Node 16，运行与验证使用 Codex 自带 Node 24。下方一键脚本会在系统 Node 过旧时自动寻找该运行时，不改变全局 Node 配置。

在项目根目录打开 PowerShell：

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
cd frontend
npm.cmd ci
cd ..
~~~

## 启动

Windows 一键后台启动：

~~~powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
~~~

脚本输出前后端进程 PID 及 taskkill 停止命令（含 Python 子进程），日志位于根目录的 frontend/backend stdout/stderr .log 文件。运行期间端口 5173 和 8000 必须可用。脚本只监听本机地址。

也可以在两个终端分别运行，按 Ctrl+C 停止：

~~~powershell
# 终端 1，项目根目录
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
~~~

~~~powershell
# 终端 2，frontend 目录；要求系统 Node 20+
npm.cmd run dev
~~~

- 页面：http://127.0.0.1:5173
- 后端接口文档：http://127.0.0.1:8000/docs
- 健康检查：GET /api/health
- 生成行程：POST /api/itineraries

Vite 将 /api 代理到后端，浏览器使用同源请求。生产构建输出不自带代理，正式部署需配置反向代理；本阶段是本地开发演示。

## 数据规则

- 目的地 1–60 字；天数 1–14；人数 1–20；兴趣至少一项。
- 日期允许 2000-01-01 至 2100-12-18，确保最长行程不会越过 2100 年。
- 预算大于 0、最多 1000 万且最多两位小数。支持 CNY、USD、EUR、JPY。
- 预算与费用均为**所有旅客的活动总额**，不含往返交通、住宿和额外购物。币种作为计价单位，不进行汇率转换。
- 演示每天 4 项活动；真实模式每天允许 1–12 项。模拟成本约占预算 70%，按分向下取整。极低预算可能展示零费用，这只是预算分配模拟，不代表真实免费。
- 不保存用户输入，刷新页面会重置。

## 检查

~~~powershell
# 根目录：接口测试（费用合计、日期、边界及非法输入）
.\.venv\Scripts\python.exe -m unittest backend.test_main -v

# frontend 目录；Node 20+，前后端已启动
npm.cmd run build
npx.cmd playwright test
~~~

浏览器测试使用本机 Google Chrome（未安装时可安装 Chrome，或修改 playwright.config.ts 去掉 channel，然后运行 npx playwright install chromium）。测试包含 1440px 桌面及 390px 手机视口，覆盖实际接口生成、加载状态、校验、重新生成、服务器失败与重试恢复。截图保存于 frontend/test-results。

系统 Node 过旧时，可在 frontend 目录使用：

~~~powershell
$travelNode = "$env:USERPROFILE/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
& $travelNode node_modules/typescript/bin/tsc -b
& $travelNode node_modules/vite/bin/vite.js build
& $travelNode node_modules/@playwright/test/cli.js test
~~~

## 文件

- frontend/src/App.tsx：表单、请求状态、结果卡片。
- frontend/src/styles.css：响应式样式及 CSS 旅行插画，无外部字体/图片请求。
- backend/main.py：请求校验及模拟行程 API。
- backend/test_main.py、frontend/tests：自动验证。
- start.ps1：本地一键启动。

## 第一阶段：模式与数据契约

第一阶段完成模块拆分与契约扩展，第二阶段完成服务适配层；第三阶段 live 已接入 Agent。沿用现有依赖。

- `backend/schemas.py`：请求校验、来源、费用与模式契约。每天允许 1–12 项活动；演示生成器仍固定四项。
- `backend/demo.py`：保留随机兴趣模板与预算约 70% 分配逻辑。
- `backend/config.py`：仅从进程环境读取配置，在创建应用时固定；修改变量后需重启后端。
- `backend/main.py`：模式分发、健康检查和结构化错误。

### 环境变量怎么传入

`backend/.env.example` **仅是变量名称示例，不会自动加载**。复制、编辑或重命名该文件不会改变服务配置；本项目没有 dotenv 加载器。

请在项目根目录 `D:\Coding\ai-travel-planner` 的 PowerShell 中启动后端：

~~~powershell
$env:TRAVEL_MODE = "demo"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
~~~

进程会继承启动终端的环境变量。前端在另一个未配置密钥的终端启动；浏览器只请求本地 `/api`。环境变量修改不会影响已经运行的后端，需要停止并重新启动。

一键启动脚本同样继承运行脚本的终端环境：

~~~powershell
$env:TRAVEL_MODE = "demo"
powershell -ExecutionPolicy Bypass -File .\start.ps1
~~~

如只验证 live 的缺配置提示，可以仅设置 `$env:TRAVEL_MODE = "live"` 后启动。**演示与 mock 测试不需要真实密钥。**

| 变量 | 默认或要求 |
| --- | --- |
| TRAVEL_MODE | 默认 demo，仅允许 demo/live；无效值直接启动失败 |
| DASHSCOPE_API_KEY | live 配置项，仅由后端读取 |
| LLM_BASE_URL | live 必填，需有效 HTTPS 地址且不含凭据、查询参数或片段 |
| LLM_MODEL | 默认 qwen3.7-plus |
| BOCHA_API_KEY | live 配置项，仅由后端读取 |
| BOCHA_BASE_URL | 默认 https://api.bocha.cn |

`GET /api/health` 保留 `status/demo`，补充 `mode/configured/ready/live_integration_available/supported_currencies/configuration_issues/message`。健康检查不探测外部网络，configured 只代表当前模式的本地配置有效。

- demo：ready=true，无需密钥，支持四种币种。
- live 缺配置：configured=false、ready=false；行程请求返回 HTTP 503，code 为 LIVE_NOT_CONFIGURED。
- live 配置齐全：configured=true、ready=true、live_integration_available=true；行程请求调用 Agent。ready 只表示本地配置与入口就绪，不验证外部连通性。
- live 非 CNY 请求：HTTP 422，code 为 UNSUPPORTED_CURRENCY。
- 所有 live 路径均不回退 demo。前端在服务状态未知或不可用时禁止生成。
- 错误格式：`{"error":{"code":"…","message":"中文说明","retryable":false,"request_id":"…"}}`。

### 费用、来源与未知值

已知金额保持两位小数字符串；未知为 JSON null，不是 "0.00"。活动增加 `cost_status/cost_basis/sources/opening_hours/opening_hours_status`。cost_status 为 reference（来源参考价）、estimated（估算）、unknown（未知）、demo（模拟）；有金额时必须说明依据。sources 含 id、title、HTTP/HTTPS url 和带时区的 retrieved_at。

营业时间状态 reported 只表示来源记载，不能保证未来营业；unknown 时营业时间必须为 null。演示没有真实来源或营业时间，保留模拟地点标记。

后端从活动重新计算当天和全程费用：有任何未知费用时完整合计为 null；`known_cost_subtotal` 保存已知费用小计。前端显示“费用不完整”和小计，不把未知显示成免费。通用契约允许每天 1–12 项活动；真实模式地点必须带来源，不能使用 demo 费用状态。模式与 demo 布尔标记必须一致。

### 测试基线与卡住原因

原测试卡在 Windows Python 3.12 的 `socket.socketpair()`，由 TestClient/AnyIO 初始化 asyncio 时触发；堆栈位于 `socket.accept`，尚未进入行程逻辑。同一解释器在本次受限执行环境外可正常建立本地连接，原始四项测试通过。

测试模块现已通过 `backend/test_support.py` 在独立子进程执行 5 秒限时预检。若执行环境阻止本地回环，会快速报出明确原因，不再无限停留；预检不会访问外部服务，也不会跳过业务测试。请在可使用本地回环的外部 PowerShell 运行：

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest backend.test_main -v
~~~

这不要求修改系统 Python、安装依赖或更改项目业务逻辑。测试使用显式 Settings/create_app 隔离环境，并验证 demo/live 路径没有外部 HTTP 请求。

浏览器测试中的真实模式数据是本地拦截夹具，仅用于验证来源、未知费用与可变活动数量的渲染；它们不是模型或搜索接入验收。

一键脚本在启动前端进程时暂时移除 DASHSCOPE_API_KEY 与 BOCHA_API_KEY，并恢复父进程变量；后端正常继承配置，密钥不传给 Vite。

## 第二阶段：千问与博查服务适配层

第二阶段实现独立适配层；当时尚未接入 Agent。第三阶段现已连接 live 行程接口，见下文。没有模拟回退或第二家服务自动切换，没有新增依赖或修改主要页面。

### 官方文档核实（2026-10-09）

- 默认模型 ID `qwen3.7-plus` 已在[官方模型表](https://help.aliyun.com/zh/model-studio/vision-model)确认；其快照 `qwen3.7-plus-2026-05-26` 也在表中。适配层仅接受这两个已核实的 ID，其他模型明确报 UNSUPPORTED_MODEL。
- [Function Calling 官方文档](https://help.aliyun.com/zh/model-studio/qwen-function-calling)列出 Plus 系列支持；关闭思考，使用 Chat Completions。该文档指出 Qwen 的 `required` 不能保证调用，因此强制策略须指定已注册函数名。适配层校验返回函数与调用 ID，不执行工具。
- [JSON Schema 官方文档](https://help.aliyun.com/zh/model-studio/qwen-structured-output)列出 Plus 系列支持 `response_format=json_schema`。传入后端 Pydantic 模型 Schema，strict=true；仍进行本地 JSON 与 Pydantic 校验。应定义结构固定、extra="forbid" 的输出模型。结构化生成与工具决策使用独立请求，不设置 max_tokens，格式错误不在本阶段自动修复。
- 北京地域：[API Key 文档](https://help.aliyun.com/zh/model-studio/get-api-key)给出 `https://dashscope.aliyuncs.com/compatible-mode/v1`；当前 Function Calling 示例使用 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。**填写控制台提供的同地域、同业务空间地址**，把 WorkspaceId 替换为实际值；适配层在末尾追加 /chat/completions，不自动更换域名。模型权限与额度仍需账户侧验证。
- 博查使用其[官方适配器文档](https://github.com/bocha-ai/dsh-web-search-bocha/blob/main/README.zh.md)确认的 POST `https://api.bocha.cn/v1/web-search`、Bearer 鉴权和网页结果映射。只请求网页搜索，不使用生成答案或抓取来源网页。

### 本地密钥配置

在**后端专用** PowerShell 中，进入项目根目录。以下输入仅在本地进行，不把密钥写进命令历史、代码、前端或聊天：

~~~powershell
Set-Location D:\Coding\ai-travel-planner
$env:TRAVEL_MODE = "live"
$env:DASHSCOPE_API_KEY = [Net.NetworkCredential]::new("", (Read-Host "百炼北京地域 API Key" -AsSecureString)).Password
$env:BOCHA_API_KEY = [Net.NetworkCredential]::new("", (Read-Host "博查 API Key" -AsSecureString)).Password
# 按控制台实际地址填写；下例是北京标准兼容地址
$env:LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:LLM_MODEL = "qwen3.7-plus"
$env:BOCHA_BASE_URL = "https://api.bocha.cn"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
~~~

准备百炼北京地域账户的模型权限、有效额度，以及博查账户的可用额度。[博查控制台](https://open.bocha.cn/)用于获取其密钥。先停止旧后端以释放端口；变量仅影响此终端及其新启动的子进程，修改后须重启。前端使用另一终端，不配置这些变量；start.ps1 也会阻止两项密钥传给 Vite。

`backend/.env.example` 只是参考清单，**不会自动读取**，复制成 .env 同样不会生效。仅演示页面时设置 TRAVEL_MODE=demo；需要真实生成时设 live 并配置密钥。密钥配齐只表示配置有效；没有真实连通证据，健康检查也不发送付费请求。

### 适配层契约与调用限制

`backend/providers.py` 的 `ServiceAdapters(settings, client, budget)` 由后端 Agent 使用。调用方负责 `httpx.AsyncClient` 的 async with 生命周期；同一顶层请求的所有适配层调用必须共享一个 `CallBudget(settings, request_id=...)`，不能每次调用重新创建预算。测试使用 MockTransport，无 DNS 或服务网络访问。

- `complete(messages, tools=..., tool_choice=..., output_model=...)` 返回 ModelResult：assistant message、原生工具调用（id/name/arguments）、可选 parsed Pydantic 实例及 usage。保留工具消息的 tool_call_id 供后续轮次回传；参数 JSON 必须是对象。完整工具参数业务校验、执行循环和一次格式修复属于第三阶段。
- `search(query)` 返回 SearchResult：query、results、warnings。结果含稳定来源 ID、标题、实际 HTTP/HTTPS URL、摘要、UTC 检索时间及可选来源发布日期文本。取 summary，缺失时用 snippet；去重、移除 URL 片段、丢弃非法条目，最多五条，标题最多 200 字、摘要最多 1,500 字。没有查到的价格和营业时间不会由适配层补齐。
- 有效空结果返回 results=[] 与明确警告；缺列表或全部条目无效返回 INVALID_RESPONSE，不能伪装成正常空结果。未请求或解析出真实信息时不生成模拟来源。
- demo 模式主动调用适配层也会报 DEMO_MODE，不发送网络请求。

| 环境变量 | 默认值 | 允许范围 |
| --- | --- | --- |
| TRIP_TIMEOUT_SECONDS | 120 | >0 且 ≤600 秒 |
| LLM_TIMEOUT_SECONDS | 45 | >0 且 ≤300 秒 |
| SEARCH_TIMEOUT_SECONDS | 10 | >0 且 ≤60 秒 |
| MAX_MODEL_CALLS / MAX_SEARCH_CALLS | 5 / 6 | 各 1–20 |
| MAX_MODEL_HTTP_ATTEMPTS / MAX_SEARCH_HTTP_ATTEMPTS | 6 / 8 | 各 1–40 |
| MAX_UPSTREAM_RETRIES | 1 | 0–3 |

“calls”计逻辑请求，“http_attempts”计实际请求（含重试）；任一上限或总时限到达即停止。每次尝试同时受完整请求超时、HTTPX 分阶段超时和剩余总期限限制。重试仅限网络故障、请求超时、429、5xx，默认最多一次，退避从 0.25 秒开始；Retry-After 支持秒数与 HTTP 日期，不足剩余时限则立即结束。重定向不跟随，取消向上传递且不重试。客户端断开后的取消及全局并发限制由第三阶段 Agent/接口接入处理，本阶段预算范围是适配层调用。

ProviderError 只暴露 provider/code/中文 message/retryable，不暴露上游正文或密钥：

| 类别 | code |
| --- | --- |
| 配置/演示/未知模型/非法参数 | CONFIGURATION_ERROR / DEMO_MODE / UNSUPPORTED_MODEL / INVALID_ARGUMENT |
| 鉴权/额度/拒绝请求 | AUTH_FAILED / QUOTA_EXHAUSTED / UPSTREAM_REJECTED |
| 可重试故障 | RATE_LIMITED / UPSTREAM_UNAVAILABLE / NETWORK_ERROR / UPSTREAM_TIMEOUT |
| 总期限/预算 | TOTAL_TIMEOUT / CALL_LIMIT |
| 异常协议与模型结果 | INVALID_RESPONSE / INVALID_MODEL_OUTPUT / MODEL_EMPTY / MODEL_TRUNCATED / MODEL_REFUSED |

已识别的结构化额度错误不重试；无法识别的上游错误保留明确分类，不将其消息直接展示给用户。日志仅含预算 request_id、耗时、计数、错误码、数值 usage；不记录密钥、完整输入或搜索正文。适配层错误到行程 HTTP 错误的映射将在第三阶段接入。

### 第二阶段验收与未完成项

~~~powershell
# 项目根目录：全部后端基线和 HTTP mock 测试
.\.venv\Scripts\python.exe -B -m unittest backend.test_main backend.test_providers -v
# frontend 目录：现有类型检查、构建、浏览器回归
npm.cmd run build
npx.cmd playwright test
~~~

HTTP mock 覆盖请求地址与鉴权、原生工具协议、JSON Schema、搜索归一化、401/403、429、5xx、网络故障、请求/总时限、空结果、异常响应、重试与共享预算、取消和日志脱敏；demo/live 不回退的接口基线保持。

**真实连通验证未完成**：未提供真实密钥，本次没有向千问或博查发送实际 API 请求。mock 通过不能证明本机连通性、账户权限/额度、搜索覆盖或实际模型输出质量。第三阶段已实现 Agent 搜索循环、结构化生成、来源关联与接口集成；真实行程样例验收仍待执行。用户已在普通 PowerShell 报告两家 API 连通，本轮工具进程仍看不到密钥。

### 密钥就绪后的最小连通验证（尚未执行）

在已配置上述变量的后端 PowerShell、项目根目录手动运行。独立于页面：模型一次短回答、博查一次查询，自动重试关闭，实际 HTTP 各最多一次。失败先分析错误，再决定是否追加验证；不生成完整行程。

~~~powershell
@'
import asyncio
import httpx
from backend.config import Settings
from backend.providers import CallBudget, ProviderError, ServiceAdapters

async def main():
    settings = Settings.from_env()
    if settings.mode != "live" or not settings.configured:
        print("未调用：请先在此后端终端配置 live、两项密钥及模型地址。")
        return
    settings = settings.model_copy(update={
        "max_upstream_retries": 0,
        "max_model_calls": 1, "max_model_http_attempts": 1,
        "max_search_calls": 1, "max_search_http_attempts": 1,
    })
    budget = CallBudget(settings)
    async with httpx.AsyncClient() as client:
        adapters = ServiceAdapters(settings, client, budget)
        try:
            result = await adapters.complete([{"role": "user", "content": "只回复 OK。"}])
            print("模型连通成功；usage=", result.usage)
        except ProviderError as exc:
            print("模型失败：", exc.code, exc.message)
        try:
            result = await adapters.search("杭州 西湖 官方网站")
            print("搜索连通成功；结果数=", len(result.results), "；warnings=", result.warnings)
        except ProviderError as exc:
            print("搜索失败：", exc.code, exc.message)
    print("实际调用计数：", budget.counts)

asyncio.run(main())
'@ | & .\.venv\Scripts\python.exe -B -
~~~

额度分类参考[百炼错误码](https://help.aliyun.com/zh/model-studio/error-code)与[免费额度状态文档](https://help.aliyun.com/zh/model-studio/model-usage-statistics)，包含 Arrearage、isv.OUT_OF_SERVICE、AllocationQuota.FreeTierOnly，均不自动重试。

后续接续先查看 [PROGRESS.md](PROGRESS.md)，无需重新阅读全部项目。

## 第三阶段：真实行程 Agent

已接入 `backend/agent.py` 与 `/api/itineraries`：首轮指定 `search_travel_info` 原生工具调用；后端校验 query/purpose、执行博查并按 tool_call_id 回传结果；最多三轮决策，随后移除工具，要求 JSON Schema 输出。所有调用共享原有总期限与调用预算；默认最多 3 轮搜索、6 次搜索、5 次模型请求（含一次输出修复），实际 HTTP 上限仍为搜索 8／模型 6（含重试）。新增 `MAX_SEARCH_ROUNDS=3`（1–3）与 `MAX_CONCURRENT_TRIPS=2`（1–10，每个后端 worker 独立限制）。

模型不能提供来源 URL、人数、币种或合计；后端使用原请求生成确定字段、登记来源 ID 并汇总 Decimal 金额。校验天数/日期、每日 1–12 项、时间顺序及不重叠、地点在相关来源原文中出现、已知金额与营业时间的原文依据。未知费用为 null；任何未知会令完整合计为 null，保留已知费用小计。参考价核对原文金额或按人数计算；估算仍需来源价格依据和说明，不保证市场实际报价。来源内容及用户文本不能覆盖系统规则。上述检查是形式与证据关联校验，不能替代地点、价格和开放时间的人工核实。

搜索全部失败／无证据明确报错；部分失败可依据成功资料继续，并显示警告。仅 JSON/Schema/业务输出错误允许一次无工具修复；拒绝、空输出和截断直接失败。上游错误 HTTP 502，总超时 504，配置不可用 503，并发满 429；错误包含 code/message/retryable/request_id，绝不切换 demo。总期限或客户端断开会取消剩余任务；已发出的请求仍可能计费。

### 本地一次真实验收（尚未由本轮工具执行）

停止旧后端，在已设置两项密钥及匹配百炼地址的同一 PowerShell 启动。不要把密钥复制到聊天或前端。`.env.example` 不自动加载。

~~~powershell
$env:TRAVEL_MODE = "live"
# 首次验收限制预算：零重试，最多两次搜索、一次决策加生成与一次格式修复
$env:MAX_UPSTREAM_RETRIES = "0"
$env:MAX_SEARCH_ROUNDS = "1"
$env:MAX_SEARCH_CALLS = "2"
$env:MAX_SEARCH_HTTP_ATTEMPTS = "2"
$env:MAX_MODEL_CALLS = "3"
$env:MAX_MODEL_HTTP_ATTEMPTS = "3"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
~~~

刷新 http://127.0.0.1:5173/，应显示真实模式且允许生成。仅提交一次杭州、1 天、1 人、CNY 小样例。检查 demo=false、来源可点击、未知不显示免费、营业时间可追溯、已知费用小计正确；失败先分析中文错误，不反复点击。本轮工具进程无法看到密钥，付费真实生成验收仍未完成。用户此前报告两家 API 连通，不等于此次 Agent 链路已通过真实验收。

### 可复现的离线浏览器闭环

`backend.e2e_mock` 仅用于测试，使用测试密钥和 MockTransport，绝不连接真实服务。启动测试后端：

~~~powershell
.\.venv\Scripts\python.exe -B -m backend.e2e_mock
~~~

将独立 Vite 测试页代理到 8001（frontend 目录，Node 20+）：

~~~powershell
node --input-type=module -e "import {createServer} from './node_modules/vite/dist/node/index.js'; const server=await createServer({server:{host:'127.0.0.1',port:5174,strictPort:true,proxy:{'/api':'http://127.0.0.1:8001'}}}); await server.listen(); server.printUrls();"
~~~

另一个 frontend PowerShell 运行：

~~~powershell
$env:TRAVEL_E2E_BASE_URL = "http://127.0.0.1:5174"
$env:TRAVEL_AGENT_E2E_BACKEND = "http://127.0.0.1:8001"
npx.cmd playwright test --workers=1
~~~

默认页面测试仍使用 5173；新增 Agent 浏览器测试仅在显式设置测试后端变量时运行，默认跳过，不会访问付费服务。测试覆盖页面到真实后端 Agent 再到 HTTP mock 的流程，与真实外部 API 验收分开记录。

## 第四阶段：真实模式页面验收

真实模式就绪时显示“真实模式 · AI 搜索辅助”，不可用时显示对应配置／服务状态；演示模式明确无外部调用。生成期间提示等待，禁用重复提交，真实请求 130 秒超时后恢复操作，不自动重试付费请求。

后端结构化错误保留中文原因、错误代码和请求编号，便于安全排查；不展示密钥或完整上游响应。重新生成失败保留“上次成功结果”，编辑表单也不会清掉该标记，只有新请求成功才清除。来源链接、营业时间来源／未知标记、参考价／估算依据、完整费用未知及已知小计继续使用现有契约；前端拒绝 live 中模拟费用、缺来源及非 CNY。

本轮 Chrome 桌面和手机 26 项、后端 101 项、类型检查和生产构建通过；页面测试使用隔离的本地 HTTP mock，不调用付费服务。用户已报告一次真实生成成功，尚未提供样例参数、耗时、usage 或来源人工核查结果，不能据此宣称多城市和多日质量验收完成。下一阶段按既定计划进行有限真实样例验收；先记录已有结果，再决定必要调用，避免重复消耗额度。


### 第五阶段真实样例验收（2026-10-09）

七个合成样例实际调用本地 live 后端：杭州／成都／北京各 1 天与 3 天，以及杭州 14 天。五个返回行程；杭州 3 天费用证据校验失败，14 天上游超时。所有返回的价格和营业时间均未知，未回退演示。具体耗时、请求编号与质量问题见 PROGRESS.md；不能将五次 HTTP 200 描述为准确性验收通过。

本轮补充拦截常见城市名及已知泛称地点（如“地道的美食”“附近餐厅”），保持一次无工具修复与原调用预算；加强餐厅检索和描述证据提示。提示不是事实验证，第三方来源、多日重复和餐厅覆盖仍待完善。新校验经 mock 验证，运行中的旧后端须在原有密钥终端 Ctrl+C 后重启才能生效：

~~~powershell
$env:MAX_UPSTREAM_RETRIES = "0"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
~~~

本轮不再追加真实调用；下一轮最小复核前确认密钥由该终端继承。无需重新安装依赖，.env.example 不会自动加载。
