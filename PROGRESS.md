# 项目进度

## 当前状态

- 工作目录 D:\Coding\ai-travel-planner，技术栈 React 19／TypeScript／Vite＋FastAPI／Pydantic／httpx。
- 第一、二阶段已完成，第三阶段 Agent 和 live 行程接口已实现并通过离线验收；真实行程生成尚未验收。
- demo 无外部请求；live 配置齐全后健康 ready=true，缺配置明确不可用，任何失败都不回退模拟数据。健康检查不调用付费服务，ready 不能代表上游已连通。
- 当前分支 codex/model-output-diagnostics（基于已推送的 codex/travel-agent，537ffee）。首次提交 3528a63 已推送 main；本阶段只提交第三阶段相关文件，保留第二阶段适配层、验证脚本、AGENTS.md、启动编码修复以及 README 标题的既有未提交修改。由用户决定是否合并 main。
- 每次新任务先读取 AGENTS.md、README.md、本文件，再核对实际代码与 Git。

## 已完成阶段

- 第一阶段：拆分 schemas/demo/config，健康与模式配置，来源／未知费用／状态契约和前端渲染。demo 每天四项，通用契约允许 1–12 项。
- 第二阶段：千问 Chat Completions、原生工具协议和 JSON Schema；博查归一化、HTTP(S) 来源、去重截断；共享总期限／逻辑调用／HTTP 尝试预算、有限重试和安全错误分类。未增加 SDK 或依赖。
- 第三阶段：agent.py 原生搜索循环、参数校验、证据登记、无工具结构化生成、一次修复；校验日期／数量／时间顺序／地点及价格和营业时间证据；用原请求确定字段并以 Decimal 汇总。main.py 接入真实模式、并发门禁、总超时与 ASGI 断开取消。
- 浏览器验收：新增可复现 e2e_mock 测试后端和 Agent 页面测试；前端业务代码不变，只有测试配置及用例更新。
- 跨聊天上下文和功能分支规则已整理；Git 初始化完成，禁止 force push 和自动合并 main。

## 正在进行

- 真实验收已收到安全诊断：首轮模型返回在 finish_tool_consistency 被拒绝，模型一次、搜索零次；问题位于工具响应解释，尚未进入搜索或行程生成。旧代码对 finish_reason 与工具列表做双向一致性断言，会误拒 stop 携带完整原生工具调用。
- 已修正为依据完整校验后的原生 tool_calls 执行：兼容 stop＋合法工具调用；tool_calls 结束标记但缺调用仍报 tool_calls_missing。截断、拒绝、未知工具、非法参数、空／重复 ID 和禁用工具仍被拒绝；未放宽来源或行程 Schema，也未加调用次数。
- 保留前轮安全阶段／检查名／字段诊断及具体反馈修复；新增 mock 证明 stop 携带工具能继续搜索并正确回传调用 ID。没有付费调用；原真实请求未保存响应原文，修复后的真实闭环仍待用户重启后端后仅验收一次。
- 本轮按既有规则仅提交模型适配器、Agent 修复反馈、诊断回归测试及进度；其他既有搜索适配、测试、启动编码和工作规则修改继续保留。

- 第三阶段代码与离线测试完成，等待用户在带密钥的普通 PowerShell 重启后端后进行一次真实小样例。
- 本轮工具仅检查配置存在性：DASHSCOPE_API_KEY、BOCHA_API_KEY、LLM_BASE_URL 均不可见；没有读取或打印密钥，也没有付费请求。
- 旧后端没有 --reload，不会自动载入新代码；不要为测试重启 Codex 或传递密钥到聊天。

## 下一步

1. 在原带密钥且运行 uvicorn 的 PowerShell 按 Ctrl+C，再启动后端；前端保持运行。只测试一次杭州 1 天，若失败提供新页面消息或 phase/check/fields/request_id 诊断行（若为 tool_calls_missing 表示上游未提供可执行调用），不能只提供 HTTP 502 访问日志。不要传密钥或完整响应。
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

工具结束标记兼容性修复本轮实际检查（2026-10-09）：
- Agent／provider／诊断受影响测试 67 项通过；最终全部后端 93 项通过，7.387 秒。
- TypeScript tsc -b 通过，Vite 生产构建通过，2.54 秒；前端业务未变，本轮未重复浏览器测试。
- 10 项诊断用例覆盖安全字段、具体修复反馈及 stop＋原生工具闭环；非法工具／参数／ID／禁用工具和缺失工具 payload 均继续拒绝。
- 核实百炼官方工具文档 https://help.aliyun.com/zh/model-studio/qwen-function-calling：示例依据 tool_calls 决定工具执行。本项目以此采取兼容性处理；不把 stop 本身当成成功证据，也不从自然语言猜工具。
- 实际付费请求零次；真实修复结果尚待本地带密钥终端验收。没有声称 mock 成功等于真实生成成功。

模型输出诊断本轮实际检查（2026-10-09）：
- 7 项新增 HTTP mock 诊断测试覆盖 JSON、Schema、额外字段名脱敏、缺失字段、工具参数、用量及具体反馈修复；与 Agent 测试共 26 项通过。
- 最终完整后端回归 90 项通过，7.196 秒；TypeScript 检查通过，Vite 构建通过，2.65 秒。前端业务未改，不重复浏览器回归。
- 没有真实 API 请求；截图和旧访问日志只能定位为模型校验失败，尚无真实失败阶段信息。

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

2026-10-09（Asia/Shanghai）：已修正首轮原生工具响应的结束标记兼容性，93 项离线回归及类型／构建通过，真实闭环待用户重启后端后一次验收。本轮未调用付费 API。
