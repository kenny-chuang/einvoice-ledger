<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiUrl } from '../lib/urls'
import type { BudgetSummary } from '../types/api'

interface Budget { category: string; monthly_limit: number; active: boolean; start_month: string }

const month = ref(new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit' }))
const categories = ref<string[]>([])
const budgets = ref<Budget[]>([])
const summary = ref<BudgetSummary | null>(null)
const category = ref('')
const monthlyLimit = ref<number | null>(null)
const message = ref('')
const errorMessage = ref('')
const loading = ref(true)

const summaryByCategory = computed(() => new Map(summary.value?.items.map(item => [item.category, item]) ?? []))
const formatMoney = (value: number) => `NT$${new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 }).format(value)}`
const formatPercent = (value: number) => `${new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 1 }).format(value)}%`

async function load(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const [categoriesResponse, budgetsResponse, summaryResponse] = await Promise.all([
      fetch(apiUrl('categories')), fetch(apiUrl('budgets')), fetch(`${apiUrl('budgets/summary')}?month=${month.value}`),
    ])
    if (!categoriesResponse.ok || !budgetsResponse.ok || !summaryResponse.ok) throw new Error('無法載入預算資料')
    categories.value = await categoriesResponse.json() as string[]
    budgets.value = await budgetsResponse.json() as Budget[]
    summary.value = await summaryResponse.json() as BudgetSummary
    if (!category.value) category.value = categories.value[0] ?? ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '載入失敗'
  } finally { loading.value = false }
}

async function saveBudget(): Promise<void> {
  if (!category.value || !monthlyLimit.value || monthlyLimit.value <= 0) return
  const response = await fetch(apiUrl(`budgets/${encodeURIComponent(category.value)}`), {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ monthly_limit: monthlyLimit.value, active: true, start_month: month.value }),
  })
  if (!response.ok) { errorMessage.value = '預算儲存失敗'; return }
  message.value = `${category.value} 每月預算已儲存`
  monthlyLimit.value = null
  await load()
}

async function removeBudget(value: Budget): Promise<void> {
  if (!window.confirm(`確定刪除「${value.category}」預算？`)) return
  const response = await fetch(apiUrl(`budgets/${encodeURIComponent(value.category)}`), { method: 'DELETE' })
  if (!response.ok) { errorMessage.value = '預算刪除失敗'; return }
  await load()
}

onMounted(load)
</script>

<template>
  <section class="page-intro"><div><p class="eyebrow">每月固定、不遞延</p><h1>分類預算</h1><p class="supporting-text">支出包含正數品項與已分攤折扣；未分攤折扣獨立顯示。</p></div><label><span>月份</span><input v-model="month" type="month" @change="load"></label></section>
  <section v-if="summary" class="summary-grid budget-summary-grid">
    <article class="summary-card"><p class="eyebrow">總預算</p><strong>{{ formatMoney(summary.total_limit) }}</strong></article>
    <article class="summary-card"><p class="eyebrow">已使用</p><strong>{{ formatMoney(summary.total_spent) }}</strong></article>
    <article class="summary-card"><p class="eyebrow">剩餘</p><strong>{{ formatMoney(summary.total_remaining) }}</strong></article>
    <article class="summary-card"><p class="eyebrow">未分攤折扣</p><strong>{{ formatMoney(summary.unallocated_discount_total) }}</strong></article>
  </section>
  <section class="card"><div class="section-heading"><h2>設定預算</h2></div><form class="filter-form" @submit.prevent="saveBudget"><label><span>分類</span><select v-model="category"><option v-for="item in categories" :key="item">{{ item }}</option></select></label><label><span>每月預算</span><input v-model.number="monthlyLimit" type="number" min="0.01" step="0.01" required></label><button>儲存</button></form><p v-if="message" class="message success-message">{{ message }}</p><p v-if="errorMessage" class="message error-message">{{ errorMessage }}</p></section>
  <section class="card"><div class="section-heading"><h2>{{ month }} 預算狀態</h2><span>{{ budgets.length }} 個分類</span></div><p v-if="loading" class="message">載入中…</p><div v-else class="table-wrap"><table><thead><tr><th>分類</th><th>預算</th><th>已用</th><th>剩餘</th><th>使用率</th><th>月底預估</th><th></th></tr></thead><tbody><tr v-for="budget in budgets" :key="budget.category"><td>{{ budget.category }}</td><td>{{ formatMoney(budget.monthly_limit) }}</td><td>{{ formatMoney(summaryByCategory.get(budget.category)?.spent ?? 0) }}</td><td>{{ formatMoney(summaryByCategory.get(budget.category)?.remaining ?? budget.monthly_limit) }}</td><td><progress :value="Math.min(summaryByCategory.get(budget.category)?.usage_percent ?? 0, 100)" max="100"></progress> {{ formatPercent(summaryByCategory.get(budget.category)?.usage_percent ?? 0) }}</td><td>{{ formatMoney(summaryByCategory.get(budget.category)?.forecast ?? 0) }}</td><td><button class="danger-button compact-button" @click="removeBudget(budget)">刪除</button></td></tr><tr v-if="budgets.length === 0"><td colspan="7" class="empty-cell">尚未設定分類預算。</td></tr></tbody></table></div></section>
</template>
