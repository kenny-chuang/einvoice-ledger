import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BudgetsView from '../BudgetsView.vue'


describe('BudgetsView', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders category budget totals returned by the API', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      const body = url.includes('/categories') ? ['餐點']
        : url.includes('/budgets/summary') ? {
          month: '2026-07', items: [{ category: '餐點', limit: 3000, spent: 1200, remaining: 1800, usage_percent: 40, forecast: 2400 }],
          total_limit: 3000, total_spent: 1200, total_remaining: 1800, unallocated_discount_total: 52,
        } : [{ category: '餐點', monthly_limit: 3000, active: true, start_month: '2026-07' }]
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
    }))

    const wrapper = mount(BudgetsView)
    await flushPromises()

    expect(wrapper.text()).toContain('NT$3,000')
    expect(wrapper.text()).toContain('NT$1,200')
    expect(wrapper.text()).toContain('40%')
  })
})
