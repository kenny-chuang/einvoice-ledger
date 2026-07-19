<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiUrl } from '../lib/urls'

interface PurchaseFields {
  date: string
  invoice_number: string
  product_name: string
  store_name: string
  category: string
  quantity: number | null
  unit_price: number | null
  amount: number
  note?: string
}

interface PurchaseDetail {
  id: number
  is_corrected: boolean
  original: PurchaseFields
  values: PurchaseFields & { note: string }
}

interface ProductSuggestion {
  id: number
  name: string
}

const route = useRoute()
const router = useRouter()
const detail = ref<PurchaseDetail | null>(null)
const categories = ref<string[]>([])
const suggestions = ref<ProductSuggestion[]>([])
const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const form = ref({
  date: '',
  invoiceNumber: '',
  productName: '',
  storeName: '',
  category: '待分類',
  newCategory: '',
  quantity: '',
  unitPrice: '',
  amount: '',
  note: '',
})
let suggestionTimer: number | undefined

const lineId = computed(() => Number(route.params.id))
const returnTo = computed(() => {
  const value = typeof route.query.returnTo === 'string' ? route.query.returnTo : '/purchases'
  return value.startsWith('/') && !value.startsWith('//') ? value : '/purchases'
})

function formatNumber(value: number | null): string {
  if (value === null) return '-'
  return new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 3 }).format(value)
}

function formatMoney(value: number | null): string {
  return value === null ? '-' : `NT$${formatNumber(value)}`
}

function fillForm(purchase: PurchaseDetail): void {
  form.value = {
    date: purchase.values.date,
    invoiceNumber: purchase.values.invoice_number,
    productName: purchase.values.product_name,
    storeName: purchase.values.store_name,
    category: purchase.values.category,
    newCategory: '',
    quantity: purchase.values.quantity === null ? '' : String(purchase.values.quantity),
    unitPrice: purchase.values.unit_price === null ? '' : String(purchase.values.unit_price),
    amount: String(purchase.values.amount),
    note: purchase.values.note,
  }
}

async function loadDetail(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const [detailResponse, categoryResponse] = await Promise.all([
      fetch(apiUrl(`purchases/${lineId.value}`)),
      fetch(apiUrl('categories')),
    ])
    if (detailResponse.status === 404) throw new Error('找不到這筆消費明細')
    if (!detailResponse.ok) throw new Error('無法載入消費明細')
    detail.value = await detailResponse.json() as PurchaseDetail
    fillForm(detail.value)
    if (categoryResponse.ok) categories.value = await categoryResponse.json() as string[]
  } catch (error) {
    detail.value = null
    errorMessage.value = error instanceof Error ? error.message : '載入資料時發生錯誤'
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  errorMessage.value = ''
  successMessage.value = ''
  const body = new FormData()
  body.append('corrected_date', form.value.date)
  body.append('corrected_invoice_number', form.value.invoiceNumber)
  body.append('corrected_product_name', form.value.productName)
  body.append('corrected_store_name', form.value.storeName)
  body.append('corrected_category', form.value.category)
  body.append('new_category', form.value.newCategory)
  body.append('corrected_quantity', form.value.quantity)
  body.append('corrected_unit_price', form.value.unitPrice)
  body.append('corrected_amount', form.value.amount)
  body.append('note', form.value.note)
  try {
    const response = await fetch(apiUrl(`purchases/${lineId.value}`), { method: 'POST', body })
    const result = await response.json() as { detail?: string }
    if (!response.ok) throw new Error(result.detail ?? '儲存失敗')
    await router.push(returnTo.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '儲存失敗'
  } finally {
    saving.value = false
  }
}

async function reset(): Promise<void> {
  if (!window.confirm('確定要恢復這筆消費的全部原始資料嗎？')) return
  saving.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await fetch(apiUrl(`purchases/${lineId.value}/reset`), { method: 'POST' })
    const result = await response.json() as { detail?: string; purchase?: PurchaseDetail }
    if (!response.ok || !result.purchase) throw new Error(result.detail ?? '還原失敗')
    detail.value = result.purchase
    fillForm(result.purchase)
    successMessage.value = '已恢復全部原始資料'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '還原失敗'
  } finally {
    saving.value = false
  }
}

watch(() => form.value.productName, (value) => {
  window.clearTimeout(suggestionTimer)
  suggestions.value = []
  const query = value.trim()
  if (query.length < 2) return
  suggestionTimer = window.setTimeout(async () => {
    const response = await fetch(`${apiUrl('products')}?query=${encodeURIComponent(query)}`)
    if (response.ok) suggestions.value = await response.json() as ProductSuggestion[]
  }, 180)
})

watch(lineId, loadDetail)
onMounted(loadDetail)
</script>

<template>
  <p><button class="text-button" type="button" @click="router.push(returnTo)">← 返回消費紀錄</button></p>
  <section class="card page-heading">
    <div><p class="eyebrow">單筆人工修正</p><h1>修改消費明細</h1><p class="supporting-text">修正值會用於消費紀錄、統計及商品比價；CSV 原始資料仍會完整保留。</p></div>
  </section>

  <p v-if="loading" class="card message">正在載入消費明細…</p>
  <p v-else-if="errorMessage && !detail" class="card message error-message" role="alert">{{ errorMessage }}</p>

  <template v-if="detail">
    <section class="card original-data">
      <div class="section-heading"><h2>原始資料</h2><span v-if="detail.is_corrected" class="status-pill allocated">目前有人工修正</span></div>
      <dl>
        <div><dt>日期</dt><dd>{{ detail.original.date }}</dd></div>
        <div><dt>發票號碼</dt><dd>{{ detail.original.invoice_number }}</dd></div>
        <div><dt>商品</dt><dd>{{ detail.original.product_name }}</dd></div>
        <div><dt>商店</dt><dd>{{ detail.original.store_name }}</dd></div>
        <div><dt>分類</dt><dd>{{ detail.original.category }}</dd></div>
        <div><dt>數量</dt><dd>{{ formatNumber(detail.original.quantity) }}</dd></div>
        <div><dt>單價</dt><dd>{{ formatMoney(detail.original.unit_price) }}</dd></div>
        <div><dt>金額</dt><dd>{{ formatMoney(detail.original.amount) }}</dd></div>
      </dl>
    </section>

    <section class="card">
      <h2>修正後資料</h2>
      <form class="edit-grid" @submit.prevent="save">
        <label><span>日期</span><input v-model="form.date" type="date" required></label>
        <label><span>發票號碼</span><input v-model="form.invoiceNumber" required></label>
        <label class="wide"><span>商品名稱</span><input v-model="form.productName" list="edit-product-suggestions" autocomplete="off" required><datalist id="edit-product-suggestions"><option v-for="product in suggestions" :key="product.id" :value="product.name" /></datalist><small>可選擇既有標準商品，或直接輸入新的正確名稱。</small></label>
        <label class="wide"><span>商店名稱</span><input v-model="form.storeName" required></label>
        <label><span>分類</span><select v-model="form.category" required><option v-for="item in categories" :key="item" :value="item">{{ item }}</option></select></label>
        <label><span>新增分類（選填）</span><input v-model="form.newCategory" placeholder="輸入後優先使用"></label>
        <label><span>數量</span><input v-model="form.quantity" type="number" step="0.001"></label>
        <label><span>單價</span><input v-model="form.unitPrice" type="number" step="0.001"></label>
        <label><span>金額</span><input v-model="form.amount" type="number" step="0.01" required></label>
        <label class="wide"><span>修正備註</span><textarea v-model="form.note" rows="3" placeholder="例如：店家品名錯誤，149 元為組合價"></textarea></label>
        <div class="form-actions wide"><button type="submit" :disabled="saving">{{ saving ? '儲存中…' : '儲存單筆修正' }}</button><button class="secondary-button" type="button" @click="router.push(returnTo)">取消</button></div>
      </form>
      <p v-if="successMessage" class="message success-message">{{ successMessage }}</p>
      <p v-else-if="errorMessage" class="message error-message" role="alert">{{ errorMessage }}</p>
      <div v-if="detail.is_corrected" class="reset-section"><button class="danger-button" type="button" :disabled="saving" @click="reset">恢復全部原始資料</button></div>
    </section>
  </template>
</template>
