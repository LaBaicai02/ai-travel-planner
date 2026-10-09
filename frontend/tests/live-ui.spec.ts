import { test, expect, type Page } from '@playwright/test'

const health = { status: 'ok', mode: 'live', demo: false, configured: true, ready: true,
  live_integration_available: true, supported_currencies: ['CNY'], configuration_issues: [], message: '真实模式已就绪' }
const makePlan = () => ({ mode: 'live', demo: false, destination: '杭州', travelers: 2, currency: 'CNY',
  budget: '100.00', total_cost: null, known_cost_subtotal: '0.00', notice: '搜索资料辅助生成，出行前核实',
  warnings: ['部分费用未知'], request_id: 'live-ui-result', days: [{ day: 1, date: '2026-10-10', title: '西湖漫步',
    estimated_cost: null, known_cost_subtotal: '0.00', activities: [{ time: '09:00 - 10:00', location: '西湖',
      description: '建议游览', estimated_cost: null, cost_status: 'unknown', cost_basis: '', opening_hours: null,
      opening_hours_status: 'unknown', sources: [{ id: 's1', title: '官方介绍', url: 'https://example.com/westlake', retrieved_at: '2026-10-09T08:00:00Z' }] }] }] })

async function configureHealth(page: Page) {
  await page.route('**/api/health', route => route.fulfill({ json: health }))
}
async function enterRequest(page: Page) {
  await page.goto('/')
  await page.locator('#destination').fill('杭州')
  await page.locator('#days').fill('1')
}

test('真实模式就绪标记及付费提示准确', async ({ page }) => {
  await configureHealth(page)
  await page.route('**/api/itineraries', route => route.fulfill({ json: makePlan() }))
  await enterRequest(page)
  await expect(page.locator('.demo-pill')).toHaveText('真实模式 · AI 搜索辅助')
  await expect(page.locator('.form-disclaimer')).toContainText('可能产生 API 费用')
  await expect(page.getByRole('combobox', { name: '币种' }).locator('option')).toHaveCount(1)
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.locator('.day-card')).toHaveCount(1)
  await expect(page.locator('.activity-cost')).toContainText('价格未知')
  await expect(page.locator('.opening-hours')).toContainText('未知／待核实')
  await expect(page.locator('.known-cost')).toContainText('不代表完整费用')
  await expect(page.locator('.source-links a')).toHaveAttribute('href', 'https://example.com/westlake')
  await expect(page.locator('.results')).not.toContainText('NaN')
  await page.screenshot({ path: `test-results/live-ui-${test.info().project.name}.png`, fullPage: true })
})

test('后端错误保留安全代码和请求编号，手机布局无溢出', async ({ page }) => {
  await configureHealth(page)
  const requestId = '1234567890abcdef1234567890abcdef'
  await page.route('**/api/itineraries', route => route.fulfill({ status: 502, json: { error: {
    code: 'INVALID_MODEL_OUTPUT', message: '行程校验失败，请核实来源。', retryable: false, request_id: requestId,
  } } }))
  await enterRequest(page)
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.getByRole('alert')).toContainText('行程校验失败')
  await expect(page.locator('.error-details')).toContainText(requestId)
  await expect(page.locator('.error-details')).toContainText('INVALID_MODEL_OUTPUT')
  await expect(page.locator('#destination')).toBeEnabled()
  await page.setViewportSize({ width: 320, height: 700 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test('修改表单不清除上次成功结果标记，新请求成功才清除', async ({ page }) => {
  await configureHealth(page)
  let calls = 0
  await page.route('**/api/itineraries', route => {
    calls++
    return calls === 2 ? route.fulfill({ status: 504, json: { error: {
      code: 'TOTAL_TIMEOUT', message: '生成超时', retryable: true, request_id: 'test-timeout',
    } } }) : route.fulfill({ json: makePlan() })
  })
  await enterRequest(page)
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.locator('.day-card')).toHaveCount(1)
  await page.getByRole('button', { name: '重新生成', exact: true }).click()
  await expect(page.locator('.stale-result')).toContainText('上次成功结果')
  await page.locator('#budget').fill('200')
  await expect(page.locator('.stale-result')).toBeVisible()
  await expect(page.locator('.day-card')).toHaveCount(1)
  await page.getByRole('button', { name: '重新生成', exact: true }).click()
  await expect(page.locator('.stale-result')).toHaveCount(0)
  expect(calls).toBe(3)
})

test('真实模式130秒超时解除加载，禁止重复提交且不自动重试', async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-10T00:00:00Z") })
  await page.clock.pauseAt(new Date("2026-10-10T00:00:10Z"))
  await configureHealth(page)
  let calls = 0
  await page.route('**/api/itineraries', route => { calls++; void route })
  await enterRequest(page)
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.locator('.loading-state')).toContainText('正在检索旅行资料')
  await expect(page.locator('#destination')).toBeDisabled()
  await expect.poll(() => calls).toBe(1)
  await page.clock.fastForward(129999)
  await expect(page.locator('.loading-state')).toBeVisible()
  await page.clock.fastForward(2)
  await expect(page.getByRole('alert')).toContainText('超时')
  await expect(page.locator('#destination')).toBeEnabled()
  await expect(page.getByRole('button', { name: '生成我的行程' })).toBeEnabled()
  expect(calls).toBe(1)
})

test('真实结果拒绝模拟费用、缺少来源及错误币种', async ({ page }) => {
  await configureHealth(page)
  let result = makePlan()
  await page.route('**/api/itineraries', route => route.fulfill({ json: result }))
  await enterRequest(page)
  const invalid = [
    () => { result.days[0].activities[0].sources = [] },
    () => { result.currency = 'USD' },
    () => { const a = result.days[0].activities[0]; Object.assign(a, { estimated_cost: '20.00', cost_status: 'demo', cost_basis: '模拟价' }) },
  ]
  for (const change of invalid) {
    result = makePlan()
    change()
    await page.getByRole('button', { name: '生成我的行程' }).click()
    await expect(page.getByRole('alert')).toContainText('格式或服务模式不一致')
    await expect(page.locator('.day-card')).toHaveCount(0)
  }
})
