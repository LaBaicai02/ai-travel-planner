import { test, expect } from '@playwright/test'

test('校验、真实接口生成、加载和重新生成', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto('/')
  await expect(page.locator('.demo-pill')).toHaveText('演示数据')
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.locator('#destination-error')).toBeVisible()
  await page.getByRole('button', { name: '杭州', exact: true }).click()
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.locator('#destination')).toBeDisabled()
  await expect(page.getByRole('heading', { name: '正在编排你的旅程' })).toBeVisible()
  await expect(page.locator('.day-card')).toHaveCount(3)
  await expect(page.locator('.activity')).toHaveCount(12)
  await expect(page.locator('.result-notice')).toContainText('演示数据')
  await page.locator('#days').fill('1')
  await page.getByRole('button', { name: '重新生成', exact: true }).click()
  await expect(page.locator('.day-card')).toHaveCount(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  expect(errors).toEqual([])
  await page.screenshot({ path: 'test-results/result-' + test.info().project.name + '.png', fullPage: true })
  await page.setViewportSize({ width: 320, height: 700 })
  await page.locator('#destination').fill('A'.repeat(60))
  await page.getByRole('button', { name: '重新生成', exact: true }).click()
  await expect(page.locator('.result-heading h2')).toContainText('A'.repeat(60))
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test('兴趣和预算校验、失败提示与重试恢复', async ({ page }) => {
  await page.goto('/')
  await page.locator('#destination').fill('京都')
  await page.getByRole('button', { name: '自然风光', exact: true }).click()
  await page.getByRole('button', { name: '地道美食', exact: true }).click()
  await page.locator('#budget').fill('0')
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.locator('#interests-error')).toBeVisible()
  await expect(page.locator('#budget-error')).toBeVisible()
  await page.getByRole('button', { name: '历史文化', exact: true }).click()
  await page.locator('#budget').fill('1000')
  await page.getByRole('combobox', { name: '币种' }).selectOption('JPY')
  await page.route('**/api/itineraries', route => route.fulfill({ status: 500, body: '{}' }))
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.getByRole('alert')).toContainText('生成失败')
  await expect(page.locator('#destination')).toBeEnabled()
  await page.unroute('**/api/itineraries')
  await page.getByRole('button', { name: '重试生成' }).click()
  await expect(page.locator('.day-card')).toHaveCount(3)
  await expect(page.locator('.cost-summary')).toContainText('JP')
})

test('服务状态失败时禁止提交，可重新检查恢复', async ({ page }) => {
  await page.route('**/api/health', route => route.fulfill({ status: 503, body: '{}' }))
  await page.goto('/')
  await expect(page.locator('.demo-pill')).toHaveText('服务状态未知')
  await expect(page.getByRole('button', { name: '生成我的行程' })).toBeDisabled()
  await expect(page.getByRole('alert')).toContainText('服务状态未知')
  await page.unroute('**/api/health')
  await page.getByRole('button', { name: '重新检查服务' }).click()
  await expect(page.locator('.demo-pill')).toHaveText('演示数据')
  await expect(page.getByRole('button', { name: '生成我的行程' })).toBeEnabled()
})

test('真实模式缺配置明确显示不可用并仅保留人民币', async ({ page }) => {
  await page.route('**/api/health', route => route.fulfill({ json: {
    status: 'unavailable', mode: 'live', demo: false, configured: false, ready: false,
    live_integration_available: false, supported_currencies: ['CNY'],
    configuration_issues: ['DASHSCOPE_API_KEY'], message: '真实模式缺少配置：DASHSCOPE_API_KEY',
  } }))
  let generations = 0
  await page.route('**/api/itineraries', route => { generations++; return route.abort() })
  await page.goto('/')
  await expect(page.locator('.demo-pill')).toContainText('真实模式')
  await expect(page.getByRole('alert')).toContainText('缺少配置')
  await expect(page.getByRole('button', { name: '生成我的行程' })).toBeDisabled()
  await expect(page.getByRole('combobox', { name: '币种' }).locator('option')).toHaveCount(1)
  expect(generations).toBe(0)
})

test('契约渲染：两项活动、未知费用、来源与营业时间', async ({ page }) => {
  // Fixture only: this is not a live provider integration test.
  await page.route('**/api/health', route => route.fulfill({ json: {
    status: 'ok', mode: 'live', demo: false, configured: true, ready: true,
    live_integration_available: true, supported_currencies: ['CNY'], configuration_issues: [], message: '测试契约',
  } }))
  const source = { id: 's1', title: '景点官方说明', url: 'https://example.com/info', retrieved_at: '2026-10-08T08:00:00Z' }
  const common = { time: '09:00 — 10:00', location: '测试地点', description: '测试活动', sources: [source], opening_hours: null, opening_hours_status: 'unknown' }
  await page.route('**/api/itineraries', route => route.fulfill({ json: {
    mode: 'live', demo: false, destination: '杭州', travelers: 2, currency: 'CNY', budget: '100.00',
    total_cost: null, known_cost_subtotal: '20.00', notice: '信息待核实', warnings: ['部分费用未知'], request_id: 'fixture',
    days: [{ day: 1, date: '2026-10-08', title: '测试行程', estimated_cost: null, known_cost_subtotal: '20.00',
      activities: [
        { ...common, estimated_cost: null, cost_status: 'unknown', cost_basis: '' },
        { ...common, time: '12:00 — 13:00', estimated_cost: '20.00', cost_status: 'estimated', cost_basis: '测试估算依据',
          opening_hours: '09:00-17:00', opening_hours_status: 'reported' },
      ] }],
  } }))
  await page.goto('/')
  await page.locator('#destination').fill('杭州')
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.locator('.activity')).toHaveCount(2)
  await expect(page.locator('.cost-summary')).toContainText('费用不完整')
  await expect(page.locator('.known-cost')).toContainText('20.00')
  await expect(page.locator('.activity-cost').first()).toContainText('价格未知')
  await expect(page.locator('.opening-hours').first()).toContainText('未知／待核实')
  await expect(page.locator('.opening-hours').last()).toContainText('09:00-17:00')
  await expect(page.locator('.source-links a').first()).toHaveAttribute('href', source.url)
  await expect(page.locator('.source-links a').first()).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(page.locator('.results')).not.toContainText('NaN')
})

test('重新生成失败保留并标注旧结果，错误格式不冒充成功', async ({ page }) => {
  await page.goto('/')
  await page.locator('#destination').fill('杭州')
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.locator('.day-card')).toHaveCount(3)
  await page.route('**/api/itineraries', route => route.fulfill({ status: 503, json: {
    error: { code: 'LIVE_NOT_CONFIGURED', message: '测试配置错误', retryable: false, request_id: 'test' },
  } }))
  await page.getByRole('button', { name: '重新生成', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('测试配置错误')
  await expect(page.locator('.stale-result')).toContainText('上次成功结果')
  await expect(page.locator('.day-card')).toHaveCount(3)
  await page.unroute('**/api/itineraries')
  await page.route('**/api/itineraries', route => route.fulfill({ json: { days: [] } }))
  await page.getByRole('button', { name: '重试生成' }).click()
  await expect(page.getByRole('alert')).toContainText('格式')
  await expect(page.locator('.day-card')).toHaveCount(3)
  await page.unroute('**/api/itineraries')
  await page.getByRole('button', { name: '重试生成' }).click()
  await expect(page.locator('.stale-result')).toHaveCount(0)
})

test('演示请求15秒超时后恢复操作，不自动重试', async ({ page }) => {
  let requests = 0
  await page.clock.install()
  await page.route('**/api/itineraries', route => { requests++; void route })
  await page.goto('/')
  await page.locator('#destination').fill('杭州')
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await page.clock.fastForward(15001)
  await expect(page.getByRole('alert')).toContainText('超时')
  await expect(page.locator('#destination')).toBeEnabled()
  expect(requests).toBe(1)
})
