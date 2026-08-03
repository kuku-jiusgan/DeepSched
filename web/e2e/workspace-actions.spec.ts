import { expect, test, type Page } from '@playwright/test'

async function openWorkspace(page: Page) {
  await mockWorkspaceApi(page)
  await page.addInitScript(() => {
    localStorage.setItem('token', 'visual-test-token')
    localStorage.setItem('user', JSON.stringify({ username: 'visual', display_name: '视觉测试' }))
    localStorage.setItem('lastActivityAt', String(Date.now()))
  })
  await page.goto('/tasks/workspace')
  await expect(page.getByRole('heading', { name: '个人工作台' })).toBeVisible()
}

async function mockWorkspaceApi(page: Page) {
  const now = new Date()
  const planStart = new Date(now.getTime() - 60 * 60 * 1000).toISOString()
  const planEnd = new Date(now.getTime() + 3 * 60 * 60 * 1000).toISOString()
  await page.route('**/api/v1/**', route => route.fulfill({ json: {} }))
  await page.route('**/api/v1/role-permissions/me', route => route.fulfill({
    json: {
      role: '技术员',
      roles: ['技术员'],
      permissions: [{
        page_key: '/tasks/workspace',
        page_name: '个人工作台',
        group_name: '任务',
        can_view: true,
        can_operate: true,
        actions: ['start', 'complete', 'night_run', 'delay'].map(actionKey => ({
          action_key: actionKey,
          action_name: actionKey,
          allowed: true,
        })),
      }],
    },
  }))
  await page.route('**/api/v1/schedules/my-tasks', route => route.fulfill({
    json: [{
      task_id: 42,
      task_name: '方法开发',
      task_type: 'FFKF_001',
      assignee_id: 3,
      assignee_name: '陈婷婷',
      project_id: 19,
      project_name: '奥拉帕利中9个基因毒杂质方法研究',
      project_code: 'XM2026208',
      execution_status: 'running',
      est_duration_hours: 32,
      actual_duration_hours: 4.5,
      task_window: { start: planStart, end: planEnd },
      actual_window: { start: planStart, end: null },
      actionable_slot: {
        id: 25,
        instrument_id: 7,
        instrument_name: '三重四极气质联用仪',
        instrument_code: 'ZBYY-002-0007',
        plan_start: planStart,
        plan_end: planEnd,
        actual_start: planStart,
        actual_end: null,
        tier: 'confirmed',
        status: 'running',
      },
      segments: [],
      delay: { status: 'not_delayed', hours: null, reason: null, reported_at: null },
    }],
  }))
  await page.route('**/api/v1/approval-gates**', route => route.fulfill({
    json: { items: [], total: 0, page: 1, page_size: 500 },
  }))
  await page.route('**/api/v1/task-types', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/schedules/rules**', route => route.fulfill({ json: [] }))
}

async function assertActionButtonDimensions(page: Page) {
  const runningCard = page.locator('.today-card').filter({
    has: page.getByRole('button', { name: '暂停' }),
  }).first()
  await expect(runningCard).toBeVisible()
  const buttons = runningCard.locator('.today-card-actions .workspace-action-button')
  await expect(buttons).toHaveCount(4)
  const dimensions = await buttons.evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect()
    return { left: rect.left, right: rect.right, width: rect.width, height: rect.height }
  }))
  for (const dimension of dimensions) {
    expect(dimension.width).toBeGreaterThanOrEqual(88)
    expect(dimension.height).toBe(30)
    expect(dimension.left).toBeGreaterThanOrEqual(0)
    expect(dimension.right).toBeLessThanOrEqual(page.viewportSize()?.width || 0)
  }
}

test('workspace action buttons share dimensions on desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await openWorkspace(page)
  await assertActionButtonDimensions(page)
  await page.screenshot({ path: 'test-results/workspace-actions-desktop.png', fullPage: true })
})

test('workspace action buttons wrap without overlap on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openWorkspace(page)
  await assertActionButtonDimensions(page)
  await page.screenshot({ path: 'test-results/workspace-actions-mobile.png', fullPage: true })
})
