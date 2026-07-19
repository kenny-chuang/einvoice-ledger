<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiUrl } from '../lib/urls'
import type { DataQualityIssue } from '../types/api'

const items = ref<DataQualityIssue[]>([])
const issueTypes = ref<string[]>([])
const month = ref('')
const store = ref('')
const issueType = ref('')
const status = ref('open')
const loading = ref(false)
const errorMessage = ref('')

async function load(): Promise<void> {
  loading.value = true
  const parameters = new URLSearchParams({ status: status.value })
  if (month.value) parameters.set('month', month.value)
  if (store.value) parameters.set('store', store.value)
  if (issueType.value) parameters.set('issue_type', issueType.value)
  try {
    const response = await fetch(`${apiUrl('data-quality')}?${parameters}`)
    if (!response.ok) throw new Error('無法載入資料品質問題')
    const data = await response.json() as { items: DataQualityIssue[]; issue_types: string[] }
    items.value = data.items; issueTypes.value = data.issue_types
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : '載入失敗' }
  finally { loading.value = false }
}

async function resolve(issue: DataQualityIssue): Promise<void> {
  if (!window.confirm('確認這筆資料可以納入統計與商品比價？')) return
  const response = await fetch(apiUrl(`data-quality/${issue.id}/resolve`), { method: 'POST' })
  if (!response.ok) { errorMessage.value = '處理失敗'; return }
  await load()
}

onMounted(load)
</script>

<template>
  <section class="card page-heading"><div><p class="eyebrow">可追溯的 CSV 清洗</p><h1>資料品質中心</h1><p class="supporting-text">低信心資料在確認前不會進入商品比價或分類預算。</p></div><form class="filter-form quality-filters" @submit.prevent="load"><label><span>月份</span><input v-model="month" type="month"></label><label><span>商店</span><input v-model="store" placeholder="商店名稱"></label><label><span>問題類型</span><select v-model="issueType"><option value="">全部</option><option v-for="item in issueTypes" :key="item">{{ item }}</option></select></label><label><span>狀態</span><select v-model="status"><option value="open">待處理</option><option value="resolved">已處理</option><option value="">全部</option></select></label><button>篩選</button></form></section>
  <section class="card"><div class="section-heading"><h2>問題明細</h2><span>{{ items.length }} 筆</span></div><p v-if="errorMessage" class="message error-message">{{ errorMessage }}</p><p v-else-if="loading" class="message">載入中…</p><div v-else class="table-wrap"><table><thead><tr><th>日期／發票</th><th>商店</th><th>類型</th><th>信心</th><th>說明</th><th>處理</th></tr></thead><tbody><tr v-for="issue in items" :key="issue.id"><td>{{ issue.invoice_date ?? '-' }}<small class="corrected-badge">{{ issue.invoice_number }}</small></td><td>{{ issue.store || '-' }}</td><td>{{ issue.issue_type }}</td><td><span class="status-pill" :class="issue.confidence === 'low' ? 'unallocated' : 'allocated'">{{ issue.confidence }}</span></td><td>{{ issue.message }}<small v-if="issue.repair_rule" class="corrected-badge">{{ issue.repair_rule }}</small></td><td><RouterLink v-if="issue.invoice_line_id" class="table-action" :to="`/purchases/${issue.invoice_line_id}/edit`">修正</RouterLink> <button v-if="issue.status === 'open'" class="compact-button" @click="resolve(issue)">確認</button></td></tr><tr v-if="items.length === 0"><td colspan="6" class="empty-cell">沒有符合條件的問題。</td></tr></tbody></table></div></section>
</template>
