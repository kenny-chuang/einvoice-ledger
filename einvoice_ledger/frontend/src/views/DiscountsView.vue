<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiUrl } from '../lib/urls'

interface DiscountCandidate {
  line_id: number
  name: string
  amount: number
}

interface DiscountAllocation {
  line_id: number
  name: string
  original_amount: number
  amount: number
  net_amount: number
}

interface DiscountRow {
  id: number
  name: string
  amount: number
  allocated: boolean
  reason: string
  suggestion: { line_id: number; name: string; method: string } | null
  candidates: DiscountCandidate[]
  allocations: DiscountAllocation[]
}

interface DiscountInvoice {
  id: number
  date: string
  invoice_number: string
  store: string
  discount_count: number
  total: number
  all_allocated: boolean
  discounts: DiscountRow[]
}

interface DiscountMonth {
  month: string
  label: string
  invoice_count: number
  discount_count: number
  total: number
  invoices: DiscountInvoice[]
}

interface DiscountResponse {
  summary: {
    unallocated_discount_count: number
    allocated_discount_count: number
    unallocated_invoice_count: number
    allocated_invoice_count: number
  }
  unallocated: DiscountMonth[]
  allocated: DiscountMonth[]
}

const data = ref<DiscountResponse | null>(null)
const loading = ref(true)
const errorMessage = ref('')
const actionMessage = ref('')
const busyDiscountId = ref<number | null>(null)
const selectedTargets = ref<Record<number, number[]>>({})

const totalDiscountCount = computed(() => {
  if (!data.value) return 0
  return data.value.summary.unallocated_discount_count + data.value.summary.allocated_discount_count
})

function formatNumber(value: number): string {
  return new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 3 }).format(value)
}

function formatMoney(value: number): string {
  return `NT$${formatNumber(value)}`
}

function initializeSelections(): void {
  const selections: Record<number, number[]> = {}
  for (const month of data.value?.unallocated ?? []) {
    for (const invoice of month.invoices) {
      for (const discount of invoice.discounts) {
        if (!discount.allocated) {
          selections[discount.id] = discount.suggestion ? [discount.suggestion.line_id] : []
        }
      }
    }
  }
  selectedTargets.value = selections
}

async function loadDiscounts(showLoading = false): Promise<void> {
  if (showLoading) loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetch(apiUrl('discounts'))
    if (!response.ok) throw new Error('無法載入折扣資料')
    data.value = await response.json() as DiscountResponse
    initializeSelections()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '載入資料時發生錯誤'
  } finally {
    loading.value = false
  }
}

function allocationMethod(discount: DiscountRow): string {
  const selected = selectedTargets.value[discount.id] ?? []
  return discount.suggestion && selected.length === 1 && selected[0] === discount.suggestion.line_id
    ? discount.suggestion.method
    : 'manual'
}

async function allocate(discount: DiscountRow): Promise<void> {
  const targets = selectedTargets.value[discount.id] ?? []
  if (targets.length === 0) {
    errorMessage.value = '請至少選擇一個商品'
    return
  }
  busyDiscountId.value = discount.id
  errorMessage.value = ''
  actionMessage.value = ''
  const form = new FormData()
  targets.forEach((target) => form.append('target_line_ids', String(target)))
  form.append('method', allocationMethod(discount))
  try {
    const response = await fetch(apiUrl(`discounts/${discount.id}/allocate`), { method: 'POST', body: form })
    const result = await response.json() as { detail?: string }
    if (!response.ok) throw new Error(result.detail ?? '折扣分攤失敗')
    actionMessage.value = `「${discount.name}」已平均分攤至 ${targets.length} 個品項`
    await loadDiscounts()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '折扣分攤失敗'
  } finally {
    busyDiscountId.value = null
  }
}

async function reset(discount: DiscountRow): Promise<void> {
  if (!window.confirm(`確定要取消「${discount.name}」的折扣分攤嗎？`)) return
  busyDiscountId.value = discount.id
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const response = await fetch(apiUrl(`discounts/${discount.id}/reset`), { method: 'POST' })
    const result = await response.json() as { detail?: string }
    if (!response.ok) throw new Error(result.detail ?? '取消分攤失敗')
    actionMessage.value = `「${discount.name}」已改回未分攤`
    await loadDiscounts()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '取消分攤失敗'
  } finally {
    busyDiscountId.value = null
  }
}

function scrollToSection(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
}

onMounted(() => loadDiscounts(true))
</script>

<template>
  <section class="card page-heading">
    <div>
      <p class="eyebrow">人工確認</p>
      <h1>折扣分攤</h1>
      <p class="supporting-text">每張發票只顯示一次；多選商品後，折扣會平均分攤，最後一筆吸收小數尾差。</p>
    </div>
    <div v-if="data" class="discount-status-nav">
      <button class="status-summary-button" type="button" @click="scrollToSection('unallocated')"><strong>未分攤</strong><span>{{ data.summary.unallocated_invoice_count }} 張・{{ data.summary.unallocated_discount_count }} 筆</span></button>
      <button class="status-summary-button" type="button" @click="scrollToSection('allocated')"><strong>已分攤</strong><span>{{ data.summary.allocated_invoice_count }} 張・{{ data.summary.allocated_discount_count }} 筆</span></button>
    </div>
  </section>

  <p v-if="errorMessage" class="message error-message" role="alert">{{ errorMessage }}</p>
  <p v-else-if="actionMessage" class="message success-message">{{ actionMessage }}</p>
  <p v-if="loading" class="card message">正在載入折扣資料…</p>

  <template v-if="data && !loading">
    <template v-for="section in [{ id: 'unallocated', title: '未分攤', months: data.unallocated }, { id: 'allocated', title: '已分攤', months: data.allocated }]" :key="section.id">
      <section :id="section.id" class="discount-section">
        <div class="section-heading discount-section-heading"><h2>{{ section.title }}</h2><span class="result-count">{{ section.months.reduce((sum, month) => sum + month.invoice_count, 0) }} 張發票</span></div>

        <details v-for="month in section.months" :key="month.month" class="discount-month" :open="section.id === 'unallocated'">
          <summary><strong>{{ month.label }}</strong><span>{{ month.invoice_count }} 張・{{ month.discount_count }} 筆・合計 {{ formatMoney(month.total) }}</span></summary>
          <div class="discount-invoices">
            <article v-for="invoice in month.invoices" :key="invoice.id" class="discount-invoice" :class="{ unallocated: !invoice.all_allocated }">
              <div class="discount-invoice-heading">
                <div><p class="eyebrow">{{ invoice.date }}・{{ invoice.invoice_number }}</p><h3>{{ invoice.store }}</h3><p>{{ invoice.discount_count }} 筆折扣</p></div>
                <strong class="discount-value">合計 {{ formatMoney(-invoice.total) }}</strong>
              </div>

              <div class="discount-items">
                <section v-for="discount in invoice.discounts" :key="discount.id" class="discount-item" :class="{ unallocated: !discount.allocated }">
                  <div class="discount-item-heading"><div><strong>{{ discount.name }}</strong><span class="status-pill" :class="discount.allocated ? 'allocated' : 'unallocated'">{{ discount.allocated ? '已分攤' : '未分攤' }}</span></div><strong class="discount-value">{{ formatMoney(discount.amount) }}</strong></div>

                  <template v-if="discount.allocated">
                    <p class="allocation-status">已平均分攤至 {{ discount.allocations.length }} 個品項</p>
                    <ul class="allocation-list"><li v-for="allocation in discount.allocations" :key="allocation.line_id">{{ allocation.name }}：原金額 {{ formatMoney(allocation.original_amount) }}，分攤 {{ formatMoney(allocation.amount) }}，實付 {{ formatMoney(allocation.net_amount) }}</li></ul>
                    <button class="danger-button" type="button" :disabled="busyDiscountId === discount.id" @click="reset(discount)">取消分攤</button>
                  </template>

                  <template v-else>
                    <p v-if="discount.suggestion" class="suggestion">建議套用到「{{ discount.suggestion.name }}」：{{ discount.reason }}。請確認後才會套用。</p>
                    <p v-else class="unallocated-label">未分攤折扣：{{ discount.reason }}，系統不會猜測。</p>
                    <fieldset class="candidate-list">
                      <legend>折扣套用商品（可複選）</legend>
                      <label v-for="candidate in discount.candidates" :key="candidate.line_id"><input v-model="selectedTargets[discount.id]" type="checkbox" :value="candidate.line_id"><span>{{ candidate.name }}（{{ formatMoney(candidate.amount) }}）</span></label>
                    </fieldset>
                    <button type="button" :disabled="busyDiscountId === discount.id || !(selectedTargets[discount.id]?.length)" @click="allocate(discount)">{{ busyDiscountId === discount.id ? '分攤中…' : '確認並平均分攤' }}</button>
                  </template>
                </section>
              </div>
            </article>
          </div>
        </details>
        <div v-if="section.months.length === 0" class="card empty-cell">目前沒有{{ section.title }}折扣。</div>
      </section>
    </template>
    <p class="result-count">共 {{ totalDiscountCount }} 筆折扣</p>
  </template>
</template>
