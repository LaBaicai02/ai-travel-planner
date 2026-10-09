# 项目进度

更新：2026-10-09。项目目录：D:\Coding\ai-travel-planner。

## 阶段状态

- 第一阶段完成：schemas/demo/config 拆分、模式与健康接口、未知费用和来源契约、前端渲染。
- 第二阶段代码及离线验收完成：独立千问/博查适配层，不含完整 Agent，不改变主要页面。
- 第三阶段未开始：工具执行循环、业务参数校验、证据登记、行程生成和接口集成。
- 真实连通验证未完成：最后检查当前执行进程中 DASHSCOPE_API_KEY、BOCHA_API_KEY、LLM_BASE_URL 均未配置。本轮真实服务调用 0 次。

## 关键决策

沿用 React 19/TypeScript/Vite、FastAPI/Pydantic/httpx，无新增依赖。2026-10-09 核实官方文档：默认 qwen3.7-plus，另允许已核实快照 qwen3.7-plus-2026-05-26，支持工具调用和 JSON Schema。北京接入地址必须匹配控制台地域/业务空间；博查使用 /v1/web-search。官方链接见 README 第二阶段。

千问强制策略指定注册函数名，拒绝不能保证调用的 required。适配层解析 tool_calls，不执行工具。搜索结果来源 ID 稳定、URL 校验去重、最多 5 条、摘要最多 1500 字，空结果有警告，异常响应报错。

每个顶层请求共享 CallBudget，重试计入 HTTP 尝试。默认总期限 120 秒；模型/搜索单次 45/10 秒；逻辑请求上限 5/6；实际 HTTP 上限 6/8；单次重试最多 1 次。鉴权、额度、格式错误不重试。取消向上传递，重定向不跟随。

demo 无外部请求；live 缺配置明确报错，配置齐全时行程接口仍返回 LIVE_NOT_IMPLEMENTED，健康 ready=false。绝不回退 demo。全局并发与客户端断开接入留待第三阶段。

密钥只放后端进程环境，.env.example 不自动加载；更改后重启后端，前端不接收密钥。

## 本轮实际检查

- 后端：47 项 unittest 全部通过，含原有 18 项与适配层/配置 29 项（另含参数子场景）。最后代码修正后已复核。
- TypeScript：tsc -b 通过；Vite 6.4.4 构建通过。
- Playwright：本机 Chrome 桌面/手机 14 项通过，9.9 秒。实际本地 demo API 与 live 渲染夹具均覆盖；不是实际模型验收。
- 本地健康接口：demo、ready=true、live_integration_available=false。
- mock 覆盖鉴权失败、429、5xx、网络/超时、空结果、异常响应、重试上限、共享预算、取消和日志脱敏。
- 前端源码、依赖清单未修改。完成检查后没有再改前端，不重复检查。

## 启动/测试

项目根目录 PowerShell：

~~~powershell
$env:TRAVEL_MODE = "demo"
powershell -ExecutionPolicy Bypass -File .\start.ps1
.\.venv\Scripts\python.exe -B -m unittest backend.test_main backend.test_providers -v
~~~

frontend 目录、Node 20+：

~~~powershell
npm.cmd run build
npx.cmd playwright test
~~~

本机系统 Node 过旧时，已验证的替代：

~~~powershell
$travelNode = "$env:USERPROFILE/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
& $travelNode node_modules/typescript/bin/tsc -b
& $travelNode node_modules/vite/bin/vite.js build
& $travelNode node_modules/@playwright/test/cli.js test
~~~

TestClient 初始化需要本地回环；test_support 的 5 秒预检避免受限环境阻塞 socketpair。失败时在允许回环的外部 PowerShell 执行，不跳过测试。

## 接续与下一步

密钥就绪后仅做模型/搜索各一次最小连通验证，关闭自动重试、实际 HTTP 各限 1 次。README 有手动命令；本轮未执行，不生成完整行程。失败先分析再决定是否追加调用。mock 不能替代真实接入验收。

第二阶段完成后停止。第三阶段须后续明确启动。沿用确认方案，不重复服务比较，只读取相关变更；先受影响测试，阶段末统一回归，代码未变不重复检查。常规修复直接完成，非阻塞优化记待办；同一操作无新信息连续失败两次后停止并报告原因。简要维护此文件。
