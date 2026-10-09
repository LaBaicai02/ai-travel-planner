import { test, expect } from '@playwright/test'

// Requires the explicitly started local HTTP mock server. Never uses real provider keys.
const backend = process.env.TRAVEL_AGENT_E2E_BACKEND
test.skip(!backend, 'Start backend.e2e_mock and set TRAVEL_AGENT_E2E_BACKEND')

test('页面通过真实后端 Agent 和 HTTP mock 生成行程，失败保留旧结果', async ({ page }) => {
  let fail = false
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const response = await route.fetch({ url: `${backend}/${fail ? 'fail' : 'live'}${path}` })
    await route.fulfill({ response })
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: '生成我的行程' })).toBeEnabled()
  await expect(page.locator('.demo-pill')).toContainText('真实模式')
  await page.locator('#destination').fill('杭州')
  await page.locator('#days').fill('1')
  await page.getByRole('button', { name: '生成我的行程' }).click()
  await expect(page.locator('.day-card')).toHaveCount(1)
  await expect(page.locator('.activity')).toHaveCount(1)
  await expect(page.locator('.activity')).toContainText('西湖')
  await expect(page.locator('.source-links a').first()).toHaveAttribute('href', 'https://travel.example/west-lake')
  await expect(page.locator('.cost-summary')).toContainText('费用不完整')
  await expect(page.locator('.activity-cost')).toContainText('价格未知')
  fail = true
  await page.getByRole('button', { name: '重新生成', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('鉴权失败')
  await expect(page.locator('.stale-result')).toContainText('上次成功结果')
  await expect(page.locator('.day-card')).toHaveCount(1)
})
