import { flushPromises, mount } from '@vue/test-utils'
import { RouterLinkStub } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import QualityView from '../QualityView.vue'


describe('QualityView', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows low-confidence issues and an edit action', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({
      issue_types: ['csv_repair'],
      items: [{ id: 1, issue_type: 'csv_repair', severity: 'review', confidence: 'low', repair_rule: 'column_count_mismatch', message: '需要確認', status: 'open', invoice_line_id: 99, invoice_date: '2026-07-19', invoice_number: 'AB12345678', store: '測試商店', created_at: '', resolved_at: null }],
    }), { status: 200 }))))

    const wrapper = mount(QualityView, { global: { stubs: { RouterLink: RouterLinkStub } } })
    await flushPromises()

    expect(wrapper.text()).toContain('low')
    expect(wrapper.text()).toContain('測試商店')
    expect(wrapper.text()).toContain('修正')
  })
})
