<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import PriceTrendChart from '../components/PriceTrendChart.vue'
import { apiUrl } from '../lib/urls'
import type { PriceAlert } from '../types/api'

interface ProductInfo {
  id: number
  display_name: string
  canonical_name: string
  alias_name: string | null
  category: string
  size_value: number | null
  size_unit: string | null
}

interface MemberProduct {
  id: number
  canonical_name: string
  display_name: string
}

interface PurchaseEntry {
  line_id: number
  date: string
  store: string
  quantity: number | null
  price: number
  discount_amount: number
  net_price: number | null
  net_amount: number
  has_discount: boolean
  is_corrected: boolean
}

interface StoreSummary {
  store: string
  minimum: number
  maximum: number
  average: number
  count: number
}

interface TrendEntry {
  date: string
  store: string
  price: number
}

interface ProductDetailResponse {
  product: string
  product_info: ProductInfo
  minimum: number | null
  maximum: number | null
  average: number | null
  latest: number | null
  purchase_count: number
  unit_label: string
  member_products: MemberProduct[]
  entries: PurchaseEntry[]
  chart: TrendEntry[]
  stores: StoreSummary[]
}

interface ProductSuggestion {
  id: number
  name: string
  raw_name: string
  category: string
}

const route = useRoute()
const detail = ref<ProductDetailResponse | null>(null)
const categories = ref<string[]>([])
const loading = ref(true)
const errorMessage = ref('')
const successMessage = ref('')
const saving = ref(false)
const displayNameInput = ref('')
const categoryInput = ref('待分類')
const newCategoryInput = ref('')
const aliasCandidate = ref('')
const suggestions = ref<ProductSuggestion[]>([])
const priceAlert = ref<PriceAlert | null>(null)
const targetPrice = ref<number | null>(null)
const notifyNewLow = ref(true)
const alertEnabled = ref(true)
const alertEvents = ref<{ id: number; title: string; message: string; created_at: string }[]>([])
let suggestionTimer: number | undefined

const productId = computed(() => Number(route.params.id))
const sizeLabel = computed(() => {
  const product = detail.value?.product_info
  if (!product || product.size_value === null) return '未辨識規格'
  return `${formatNumber(product.size_value)} ${product.size_unit ?? ''}`.trim()
})

function formatNumber(value: number | null): string {
  if (value === null) return '-'
  return new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 3 }).format(value)
}

function formatMoney(value: number | null): string {
  return value === null ? '-' : `NT$${formatNumber(value)}`
}

async function loadDetail(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetch(apiUrl(`products/${productId.value}/prices`))
    if (response.status === 404) throw new Error('找不到這項商品')
    if (!response.ok) throw new Error('無法載入商品明細')
    detail.value = await response.json() as ProductDetailResponse
    displayNameInput.value = detail.value.product_info.alias_name ?? ''
    categoryInput.value = detail.value.product_info.category
  } catch (error) {
    detail.value = null
    errorMessage.value = error instanceof Error ? error.message : '載入資料時發生錯誤'
  } finally {
    loading.value = false
  }
}

async function loadCategories(): Promise<void> {
  const response = await fetch(apiUrl('categories'))
  if (response.ok) categories.value = await response.json() as string[]
}

async function loadPriceAlert(): Promise<void> {
  const [alertResponse, eventsResponse] = await Promise.all([
    fetch(apiUrl(`products/${productId.value}/price-alert`)),
    fetch(`${apiUrl('notifications')}?product_id=${productId.value}&limit=10`),
  ])
  if (alertResponse.ok) {
    priceAlert.value = await alertResponse.json() as PriceAlert | null
    targetPrice.value = priceAlert.value?.target_price ?? null
    notifyNewLow.value = priceAlert.value?.notify_new_low ?? true
    alertEnabled.value = priceAlert.value?.enabled ?? true
  }
  if (eventsResponse.ok) alertEvents.value = await eventsResponse.json() as typeof alertEvents.value
}

async function savePriceAlert(): Promise<void> {
  saving.value = true
  const response = await fetch(apiUrl(`products/${productId.value}/price-alert`), {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_price: targetPrice.value, notify_new_low: notifyNewLow.value, enabled: alertEnabled.value }),
  })
  saving.value = false
  if (!response.ok) { errorMessage.value = '價格提醒儲存失敗'; return }
  successMessage.value = '價格提醒已儲存；比較一律使用財政部原始單價'
  await loadPriceAlert()
}

async function deletePriceAlert(): Promise<void> {
  const response = await fetch(apiUrl(`products/${productId.value}/price-alert`), { method: 'DELETE' })
  if (response.ok) { priceAlert.value = null; targetPrice.value = null; notifyNewLow.value = true; alertEnabled.value = true; successMessage.value = '價格提醒已刪除' }
}

async function submitForm(path: string, values: Record<string, string>, message: string): Promise<void> {
  saving.value = true
  errorMessage.value = ''
  successMessage.value = ''
  const form = new FormData()
  Object.entries(values).forEach(([key, value]) => form.append(key, value))
  try {
    const response = await fetch(apiUrl(path), { method: 'POST', body: form })
    const data = await response.json() as { detail?: string | { message?: string } }
    if (!response.ok) {
      const detailMessage = typeof data.detail === 'string' ? data.detail : data.detail?.message
      throw new Error(detailMessage ?? '儲存失敗')
    }
    successMessage.value = message
    aliasCandidate.value = ''
    suggestions.value = []
    await Promise.all([loadDetail(), loadCategories()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '儲存失敗'
  } finally {
    saving.value = false
  }
}

async function saveDisplayName(): Promise<void> {
  await submitForm(`products/${productId.value}/alias-name`, { alias_name: displayNameInput.value }, '顯示名稱已儲存')
}

async function saveCategory(): Promise<void> {
  const selectedCategory = newCategoryInput.value.trim() || categoryInput.value
  await submitForm(`products/${productId.value}/category`, { category: selectedCategory }, '分類已套用到同名商品')
  newCategoryInput.value = ''
}

async function assignAlias(): Promise<void> {
  await submitForm(`products/${productId.value}/aliases`, { alias: aliasCandidate.value }, '商品已歸類，原始明細保持不變')
}

watch(aliasCandidate, (value) => {
  window.clearTimeout(suggestionTimer)
  suggestions.value = []
  const query = value.trim()
  if (query.length < 2) return
  suggestionTimer = window.setTimeout(async () => {
    const response = await fetch(`${apiUrl('products')}?query=${encodeURIComponent(query)}`)
    if (!response.ok) return
    const products = await response.json() as ProductSuggestion[]
    suggestions.value = products.filter((product) => product.id !== productId.value)
  }, 180)
})

watch(productId, () => Promise.all([loadDetail(), loadPriceAlert()]))
onMounted(() => Promise.all([loadDetail(), loadCategories(), loadPriceAlert()]))
</script>

<template>
  <p><RouterLink class="back-link" to="/products">← 返回商品比價</RouterLink></p>
  <p v-if="loading" class="card message">正在載入商品明細…</p>
  <p v-else-if="errorMessage && !detail" class="card message error-message" role="alert">{{ errorMessage }}</p>

  <template v-if="detail">
    <section class="card product-hero">
      <p class="eyebrow">{{ detail.product_info.category }}</p>
      <h1>{{ detail.product_info.display_name }}</h1>
      <p class="supporting-text">{{ sizeLabel }} · {{ detail.purchase_count }} 次消費</p>
      <div v-if="detail.minimum !== null" class="metrics">
        <div><small>歷史最低單價</small><strong>{{ formatMoney(detail.minimum) }}</strong></div>
        <div><small>歷史最高單價</small><strong>{{ formatMoney(detail.maximum) }}</strong></div>
        <div><small>歷史平均單價</small><strong>{{ formatMoney(detail.average) }}</strong></div>
        <div><small>最新單價</small><strong>{{ formatMoney(detail.latest) }}</strong></div>
      </div>
      <p v-else class="message">尚無可比較的消費明細單價。</p>
    </section>

    <section class="card">
      <div class="section-heading"><h2>商品管理</h2><span class="result-count">同一顯示名稱會共用分類與價格歷史</span></div>
      <p class="supporting-text">原始品名永久保留；顯示名稱留空時會使用原始品名。</p>
      <div class="management-grid">
        <form @submit.prevent="saveDisplayName">
          <label><span>修改顯示名稱</span><input v-model="displayNameInput" placeholder="空白則使用原始品名"></label>
          <button :disabled="saving">儲存顯示名稱</button>
        </form>
        <form @submit.prevent="saveCategory">
          <label><span>分類</span><select v-model="categoryInput"><option v-for="item in categories" :key="item" :value="item">{{ item }}</option></select></label>
          <label><span>新增分類（選填）</span><input v-model="newCategoryInput" placeholder="輸入後優先使用"></label>
          <button :disabled="saving">儲存分類</button>
        </form>
        <form @submit.prevent="assignAlias">
          <label><span>歸類其他既有商品</span><input v-model="aliasCandidate" list="product-suggestions" placeholder="輸入不同店家的商品名稱" autocomplete="off"></label>
          <datalist id="product-suggestions"><option v-for="item in suggestions" :key="item.id" :value="item.name" /></datalist>
          <button :disabled="saving || !aliasCandidate.trim()">歸類到目前商品</button>
        </form>
      </div>
      <p v-if="successMessage" class="message success-message">{{ successMessage }}</p>
      <p v-else-if="errorMessage" class="message error-message" role="alert">{{ errorMessage }}</p>
      <p class="member-names"><strong>目前包含的原始品名：</strong>{{ detail.member_products.map((item) => item.canonical_name).join('、') }}</p>
    </section>

    <section class="card">
      <div class="section-heading"><h2>價格提醒</h2><span class="result-count">只使用消費明細原始單價</span></div>
      <form class="filter-form" @submit.prevent="savePriceAlert">
        <label><span>目標單價（可留空）</span><input v-model.number="targetPrice" type="number" min="0" step="0.001" placeholder="例如 42"></label>
        <label><span>提醒狀態</span><select v-model="alertEnabled"><option :value="true">啟用</option><option :value="false">暫停</option></select></label>
        <label class="checkbox-label"><input v-model="notifyNewLow" type="checkbox"> 低於過去歷史最低時提醒</label>
        <button :disabled="saving">儲存提醒</button>
        <button v-if="priceAlert" class="danger-button" type="button" @click="deletePriceAlert">刪除</button>
      </form>
      <ul v-if="alertEvents.length" class="allocation-list"><li v-for="event in alertEvents" :key="event.id"><strong>{{ event.title }}</strong> — {{ event.message }}</li></ul>
      <p v-else class="supporting-text">尚無觸發紀錄。</p>
    </section>

    <section v-if="detail.chart.length" class="card">
      <h2>價格趨勢</h2>
      <PriceTrendChart :entries="detail.chart" />
    </section>

    <section class="card">
      <div class="section-heading"><h2>商店比較</h2><span class="result-count">依消費明細單價</span></div>
      <div class="table-wrap"><table><thead><tr><th>商店</th><th>最低單價</th><th>最高單價</th><th>平均單價</th><th>次數</th></tr></thead><tbody>
        <tr v-for="store in detail.stores" :key="store.store"><td>{{ store.store }}</td><td>{{ formatMoney(store.minimum) }}</td><td>{{ formatMoney(store.maximum) }}</td><td>{{ formatMoney(store.average) }}</td><td>{{ store.count }}</td></tr>
        <tr v-if="detail.stores.length === 0"><td colspan="5" class="empty-cell">尚無商店價格資料。</td></tr>
      </tbody></table></div>
    </section>

    <section class="card">
      <div class="section-heading"><h2>最近購買</h2><span class="result-count">最近 {{ detail.entries.length }} 筆</span></div>
      <div class="table-wrap"><table><thead><tr><th>日期</th><th>商店</th><th>數量</th><th>原始單價</th><th>折後單價</th><th>實付金額</th><th>備註</th></tr></thead><tbody>
        <tr v-for="entry in detail.entries" :key="entry.line_id"><td>{{ entry.date }}</td><td>{{ entry.store }}</td><td>{{ formatNumber(entry.quantity) }}</td><td>{{ formatMoney(entry.price) }}</td><td>{{ formatMoney(entry.net_price) }}</td><td>{{ formatMoney(entry.net_amount) }}</td><td><span v-if="entry.discount_amount < 0">已分攤折扣 {{ formatMoney(entry.discount_amount) }}</span><span v-else-if="entry.has_discount">該發票含未分攤折扣</span><span v-if="entry.is_corrected">{{ entry.has_discount ? '；' : '' }}人工修正</span></td></tr>
        <tr v-if="detail.entries.length === 0"><td colspan="7" class="empty-cell">尚無購買紀錄。</td></tr>
      </tbody></table></div>
    </section>
  </template>
</template>
