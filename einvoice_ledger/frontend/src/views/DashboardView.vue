<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { apiUrl } from '../lib/urls'
import type { SyncRunDetail } from '../types/api'

interface SyncRun {
  id: number
  status: string
  current_stage: string
  attempt_count: number
  message: string
  started_at: string
  finished_at: string | null
}

interface DashboardData {
  month_total: number
  uncategorized_total: number
  uncategorized_count: number
  uncategorized_product_count: number
  login_required: boolean
  data_quality_issue_count: number
  last_run: SyncRun | null
}

interface RecentPurchase { id:number; date:string; product_id:number|null; product_name:string; unit_price:number|null; net_amount:number }

interface ImportResult {
  invoices_upserted: number
  lines_created: number
  discounts: number
  detail?: string
}

const dashboard = ref<DashboardData | null>(null)
const recentPurchases = ref<RecentPurchase[]>([])
const loading = ref(true)
const errorMessage = ref('')
const syncMessage = ref('')
const syncStarting = ref(false)
const syncRun = ref<SyncRunDetail | null>(null)
const selectedFile = ref<File | null>(null)
const importing = ref(false)
const importMessage = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
let syncTimer: number | undefined

const syncLabel = computed(() => {
  if (!dashboard.value?.last_run) return '尚未同步'
  if (dashboard.value.login_required) return '需要重新登入'
  return {
    running: '同步中',
    completed: '同步完成',
    failed: '同步失敗',
    login_required: '需要重新登入',
  }[dashboard.value.last_run.status] ?? dashboard.value.last_run.status
})

function formatNumber(value: number): string {
  return new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 3 }).format(value)
}

function formatMoney(value: number): string {
  return `NT$${formatNumber(value)}`
}

function formatDate(value: string | null): string {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-TW', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

async function loadDashboard(showLoading = false): Promise<void> {
  if (showLoading) loading.value = true
  errorMessage.value = ''
  try {
    const [response, recentResponse] = await Promise.all([fetch(apiUrl('dashboard')), fetch(apiUrl('purchases?per_page=5&page=1'))])
    if (!response.ok || !recentResponse.ok) throw new Error('無法載入總覽資料')
    dashboard.value = await response.json() as DashboardData
    recentPurchases.value = (await recentResponse.json() as {items:RecentPurchase[]}).items.slice(0, 5)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '載入資料時發生錯誤'
  } finally {
    loading.value = false
  }
}

function pollSync(runId: number, attempt = 0): void {
  window.clearTimeout(syncTimer)
  syncTimer = window.setTimeout(async () => {
    const response = await fetch(apiUrl(`sync-runs/${runId}`))
    if (response.ok) syncRun.value = await response.json() as SyncRunDetail
    await loadDashboard()
    if (syncRun.value && ['queued', 'running'].includes(syncRun.value.status) && attempt < 600) {
      pollSync(runId, attempt + 1)
    } else if (syncRun.value?.status === 'completed') {
      syncMessage.value = syncRun.value.message || '同步完成'
    }
  }, attempt === 0 ? 1200 : 3000)
}

async function startSync(): Promise<void> {
  syncStarting.value = true
  syncMessage.value = ''
  try {
    const response = await fetch(apiUrl('sync'), { method: 'POST' })
    if (!response.ok) throw new Error('同步無法啟動')
    const data = await response.json() as { run_id: number; status: string }
    syncMessage.value = '已排入背景同步'
    pollSync(data.run_id)
  } catch (error) {
    syncMessage.value = error instanceof Error ? error.message : '同步無法啟動'
  } finally {
    syncStarting.value = false
  }
}

function selectFile(event: Event): void {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  importMessage.value = ''
}

async function uploadCsv(): Promise<void> {
  if (!selectedFile.value) return
  importing.value = true
  importMessage.value = ''
  const form = new FormData()
  form.append('file', selectedFile.value)
  try {
    const response = await fetch(apiUrl('imports/csv'), { method: 'POST', body: form })
    const data = await response.json() as ImportResult
    if (!response.ok) throw new Error(data.detail ?? 'CSV 匯入失敗')
    importMessage.value = `匯入完成：${data.invoices_upserted} 張發票、${data.lines_created} 筆品項、${data.discounts} 筆折扣`
    selectedFile.value = null
    if (fileInput.value) fileInput.value.value = ''
    await loadDashboard()
  } catch (error) {
    importMessage.value = error instanceof Error ? error.message : 'CSV 匯入失敗'
  } finally {
    importing.value = false
  }
}

onMounted(() => loadDashboard(true))
onBeforeUnmount(() => window.clearTimeout(syncTimer))
</script>

<template>
  <section class="page-intro">
    <div>
      <p class="eyebrow">發票記帳助手 1.0</p>
      <h1>記帳總覽</h1>
      <p class="supporting-text">查看本月支出、待整理資料與最近同步狀態。</p>
    </div>
    <button class="refresh-button" type="button" :disabled="loading" @click="loadDashboard(true)">重新整理</button>
  </section>

  <p v-if="errorMessage" class="card message error-message" role="alert">{{ errorMessage }}</p>
  <p v-else-if="loading" class="card message">正在載入總覽…</p>

  <template v-if="dashboard">
    <section class="summary-grid simple-summary-grid">
      <article class="summary-card primary-summary">
        <p class="eyebrow">本月確認消費</p>
        <strong>{{ formatMoney(dashboard.month_total) }}</strong>
        <RouterLink to="/purchases">查看消費紀錄</RouterLink>
      </article>
      <article class="summary-card">
        <p class="eyebrow">待整理</p>
        <strong>{{ dashboard.uncategorized_product_count + dashboard.data_quality_issue_count }} 項</strong>
        <p>{{ dashboard.uncategorized_product_count }} 個待分類商品、{{ dashboard.data_quality_issue_count }} 筆資料待修正</p>
        <RouterLink :to="{ name: 'purchases', query: { category: '待分類' } }">查看待分類紀錄</RouterLink>
      </article>
      <article class="summary-card" :class="{ 'warning-summary': dashboard.login_required }">
        <p class="eyebrow">同步狀態</p>
        <strong>{{ syncLabel }}</strong>
        <p>{{ dashboard.last_run?.message || '請上傳 CSV 或完成登入' }}</p>
        <small v-if="dashboard.last_run?.finished_at">{{ formatDate(dashboard.last_run.finished_at) }}</small>
        <RouterLink v-if="dashboard.login_required" to="/settings#login">前往登入續期</RouterLink>
      </article>
    </section>

    <section class="card"><div class="section-heading"><h2>最近消費</h2><RouterLink to="/purchases">查看全部</RouterLink></div><div class="table-wrap"><table><thead><tr><th>日期</th><th>商品</th><th>單價</th><th>金額</th></tr></thead><tbody><tr v-for="item in recentPurchases" :key="item.id"><td>{{ item.date }}</td><td><RouterLink v-if="item.product_id" :to="{name:'product-detail',params:{id:item.product_id}}">{{ item.product_name }}</RouterLink><span v-else>{{ item.product_name }}</span></td><td>{{ item.unit_price===null?'-':formatMoney(item.unit_price) }}</td><td>{{ formatMoney(item.net_amount) }}</td></tr></tbody></table></div></section>

    <section class="action-grid">
      <article class="card action-card">
        <h2>匯入 CSV</h2>
        <p class="supporting-text">上傳財政部下載的明細檔；重複上傳不會重複計算。</p>
        <form @submit.prevent="uploadCsv">
          <label class="file-field"><span>選擇 CSV 檔案</span><input ref="fileInput" type="file" accept=".csv,text/csv" required @change="selectFile"></label>
          <button type="submit" :disabled="importing || !selectedFile">{{ importing ? '匯入中…' : '開始匯入' }}</button>
        </form>
        <p v-if="importMessage" class="message" :class="{ 'error-message': importMessage.includes('失敗') || importMessage.includes('不正確') }">{{ importMessage }}</p>
      </article>

      <article class="card action-card">
        <h2>背景同步</h2>
        <p class="supporting-text">每天 04:15 同步當月與上月；工作階段失效時需手動輸入圖形驗證碼。</p>
        <button type="button" :disabled="syncStarting || dashboard.last_run?.status === 'running'" @click="startSync">{{ dashboard.last_run?.status === 'running' ? '同步進行中' : '立即同步' }}</button>
        <p v-if="syncRun" class="message">階段：{{ syncRun.current_stage }}（第 {{ syncRun.attempt_count }} 次嘗試）</p>
        <p v-if="syncMessage" class="message">{{ syncMessage }}</p>
      </article>
    </section>
  </template>
</template>
