# 项目进度

## 当前状态

- 工作目录 D:\Coding\ai-travel-planner，技术栈 React 19／TypeScript／Vite＋FastAPI／Pydantic／httpx。
- 第一、二阶段已完成，第三阶段 Agent 和 live 行程接口已实现并通过离线验收；真实行程生成尚未验收。
- demo 无外部请求；live 配置齐全后健康 ready=true，缺配置明确不可用，任何失败都不回退模拟数据。健康检查不调用付费服务，ready 不能代表上游已连通。
- 当前分支 codex/travel-agent。首次提交 3528a63 已推送 main；本阶段只提交第三阶段相关文件，保留第二阶段适配层、验证脚本、AGENTS.md、启动编码修复以及 README 标题的既有未提交修改。由用户决定是否合并 main。
- 每次新任务先读取 AGENTS.md、README.md、本文件，再核对实际代码与 Git。

## 已完成阶段

- 第一阶段：拆分 schemas/demo/config，健康与模式配置，来源／未知费用／状态契约和前端渲染。demo 每天四项，通用契约允许 1–12 项。
- 第二阶段：千问 Chat Completions、原生工具协议和 JSON Schema；博查归一化、HTTP(S) 来源、去重截断；共享总期限／逻辑调用／HTTP 尝试预算、有限重试和安全错误分类。未增加 SDK 或依赖。
- 第三阶段：agent.py 原生搜索循环、参数校验、证据登记、无工具结构化生成、一次修复；校验日期／数量／时间顺序／地点及价格和营业时间证据；用原请求确定字段并以 Decimal 汇总。main.py 接入真实模式、并发门禁、总超时与 ASGI 断开取消。
- 浏览器验收：新增可复现 e2e_mock 测试后端和 Agent 页面测试；前端业务代码不变，只有测试配置及用例更新。
- 跨聊天上下文和功能分支规则已整理；Git 初始化完成，禁止 force push 和自动合并 main。

## 正在进行

- 第三阶段代码与离线测试完成，等待用户在带密钥的普通 PowerShell 重启后端后进行一次真实小样例。
- 本轮工具仅检查配置存在性：DASHSCOPE_API_KEY、BOCHA_API_KEY、LLM_BASE_URL 均不可见；没有读取或打印密钥，也没有付费请求。
- 旧后端没有 --reload，不会自动载入新代码；不要为测试重启 Codex 或传递密钥到聊天。

## 下一步

1. 在原已配密钥的 PowerShell 停止旧后端并重启，刷新页面；按 README 限定零重试、一次决策、最多两次搜索和三次模型请求，仅生成一次杭州 1 天样例。
2. 抽查地点、来源、价格依据、营业时间和未知标记；若失败先分析错误，不反复付费生成。真实验收不得被 mock 通过替代。
3. 真实小样例确认后，再讨论后续真实多城市／多日验收和必要修复；本轮不扩展地图、预订、数据库或第二家模型。
4. 第二阶段及其他历史修改继续保留；没有将其混入第三阶段代码提交。

## 关键技术决策

- 沿用现有依赖和前端契约，不使用 LangChain、MCP 或模型 SDK。
- 2026-10-09 已核实千问 qwen3.7-plus 与快照 qwen3.7-plus-2026-05-26 的工具及 Schema 能力；北京地址须与账户地域／业务空间匹配。博查 POST /v1/web-search。
- 首轮指定 search_travel_info，后续 auto；生成与修复不提供工具。query/purpose 由后端校验，网页是资料，不能覆盖系统规则。工具结果按 tool_call_id 回传，来源 URL 只从搜索登记中获取。
- 默认总时限 120 秒，模型／搜索单次 45／10 秒；最多 3 轮决策、6 次搜索、5 次模型逻辑请求，HTTP 上限搜索 8／模型 6（含重试）。输出错误仅修复一次；鉴权／额度／空输出／截断／拒绝不修复。额外工具调用只获得明确超限错误。
- MAX_SEARCH_ROUNDS 默认 3（1–3），MAX_CONCURRENT_TRIPS 默认 2（1–10）；并发限制为单 worker，不是分布式限流。
- 来源最多五条／次，摘要 1500 字，HTTP(S) 且无凭据；地点须出现于相关原文。已知费用及营业时间需要相关来源原文片段，参考金额校验原价或按人数计算；这些形式检查不能保证事实正确，仍需人工抽查。
- 金额是全体旅客活动费用，两位小数字符串；未知为 null，完整总价随未知传播，另展示已知小计；超预算如实警告，不按预算缩放。
- 密钥只在后端进程环境，.env.example 不自动读取；Vite 不继承密钥。日志不记录密钥、Authorization、完整响应或旅行输入。请求取消后已发出的上游调用仍可能计费。

## 测试与验证

第三阶段本轮实际检查（2026-10-09）：
- 受影响 Agent/接口测试初轮 35 项通过；新增 14 天及价格依据边界后统一后端回归 83 项通过，7.116 秒（原 64 项＋19 项 Agent/接口测试）。
- TypeScript tsc -b 通过；Vite 6.4.4 生产构建通过，3.05 秒。
- Chrome 桌面／手机完整 16 项通过，19.8 秒；测试包含页面→后端 Agent→HTTP mock→页面，以及鉴权失败保留旧结果。夹具改为请求独立状态后仅补测受影响两项，均通过，3.3 秒。
- 首次接口测试阻塞已用限时堆栈定位并修复：断开监听改为 ASGI receive；首次临时 Vite 绑定 IPv6 导致连接拒绝，改为 127.0.0.1 后通过，未降低测试标准。
- 实际外部模型／搜索请求零次；真实行程、来源质量和耗时尚未验证。git diff --check 通过，提交前检查忽略规则与敏感信息。

历史证据（不代表本轮重新执行或真实行程验收）：
- 第一／二阶段及首次上传前 47 项后端测试、类型检查、构建和 14 项 Chrome 测试通过。
- 第二阶段适配层完善后 64 项后端通过；类型／构建通过，工具配置门禁阻止真实请求。
- 用户在普通 PowerShell 报告两家 API 成功：千问 total_tokens=48、completion_tokens=33、prompt_tokens=15；博查三条结果、无警告，搜索一次且无重试。该结果不含 HTTP 状态／耗时，不是第三阶段完整旅程验收。
- start.ps1 曾为 UTF-8 无 BOM，Windows PowerShell 5.1 将中文错误解码并报 ParserError；已补 UTF-8 BOM，Windows PowerShell Parser.ParseFile 零错误，启动逻辑未改。此历史修复仍保留在工作区。

## 已知问题

- 真实 Agent 行程质量仍未验证；搜索可能没有足够证据，系统会明确失败或显示未知，不补造信息。
- 价格／营业信息可能变化或来源冲突；链接可追溯不等于事实保证。并发上限在多 worker 时不共享。
- Windows TestClient 的 socketpair 在受限环境可能阻塞；test_support 独立子进程 5 秒预检，需允许本地回环，不跳过业务测试。
- 系统 Node 曾过旧；本轮使用已有 Node 24 运行时。无需重复安装依赖。

## 常用启动和测试命令

项目根目录 PowerShell（环境变量须在启动进程前设置）：

~~~powershell
$env:TRAVEL_MODE = "demo" # 真实模式使用 live 和本地配置的两项密钥、模型地址
powershell -ExecutionPolicy Bypass -File .\start.ps1
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -B -m unittest discover -s backend -p test_*.py -q
# 独立本地 HTTP mock 浏览器服务，不调用真实 API
.\.venv\Scripts\python.exe -B -m backend.e2e_mock
~~~

frontend 目录（Node 20+）：

~~~powershell
npm.cmd run build
npx.cmd playwright test
# 系统 Node 过旧时用已有运行时
$travelNode = "$env:USERPROFILE/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
& $travelNode node_modules/typescript/bin/tsc -b
& $travelNode node_modules/vite/bin/vite.js build
& $travelNode node_modules/@playwright/test/cli.js test
~~~

页面 5173，后端文档 8000/docs。真实小样例预算和独立 5174→8001 mock 浏览器测试命令见 README。新增 Agent 浏览器测试要求 TRAVEL_AGENT_E2E_BACKEND，默认跳过；本轮显式设置并实际执行通过。

## 最后更新时间

2026-10-09（Asia/Shanghai）：第三阶段代码和离线验收完成；真实付费行程验收待本地带密钥终端执行。没有更改前端业务功能或新增依赖。
