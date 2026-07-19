<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiUrl } from '../lib/urls'

interface PurchaseRecord {
  id: number
  date: string
  month: string
  month_label: string
  invoice_number: string
  product_id: number | null
  product_name: string
  raw_name: string
  quantity: number | null
  unit_price: number | null
  discount_amount: number
  net_unit_price: number | null
  net_amount: number
  is_corrected: boolean
}

interface PurchaseResponse {
  items: PurchaseRecord[]
  query: string
  month: string
  category: string
  page: number
  per_page: number
  total: number
  total_pages: number
}

const route = useRoute()
const router = useRouter()
const records = ref<PurchaseRecord[]>([])
const categories = ref<string[]>([])
const months = ref<string[]>([])
const query = ref('')
const month = ref('')
const category = ref('')
const perPage = ref(50)
const currentPage = ref(1)
const total = ref(0)
const totalPages = ref(1)
const loading = ref(false)
const errorMessage = ref('')

const hasFilters = computed(() => Boolean(query.value || month.value || category.value || perPage.value !== 50))

function routeText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function routeNumber(value: unknown, fallback: number): number {
  const number = Number(routeText(value))
  return Number.isInteger(number) && number > 0 ? number : fallback
}

function formatNumber(value: number | null): string {
  if (value === null) return '-'
  return new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 3 }).format(value)
}

function formatMoney(value: number | null): string {
  return value === null ? '-' : `NT$${formatNumber(value)}`
}

function shortName(name: string): string {
  const characters = Array.from(name)
  return characters.length <= 10 ? name : `${characters.slice(0, 10).join('')}...`
}

function monthLabel(value: string): string {
  const [year, monthNumber] = value.split('-')
  return `${year} 年 ${monthNumber} 月`
}

function startsMonthGroup(index: number): boolean {
  return index === 0 || records.value[index - 1]?.month !== records.value[index]?.month
}

async function loadOptions(): Promise<void> {
  const [categoryResponse, monthResponse] = await Promise.all([
    fetch(apiUrl('categories')),
    fetch(apiUrl('purchase-months')),
  ])
  if (!categoryResponse.ok || !monthResponse.ok) throw new Error('無法載入篩選選項')
  categories.value = await categoryResponse.json() as string[]
  months.value = await monthResponse.json() as string[]
}

async function loadRecords(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  const parameters = new URLSearchParams({
    page: String(currentPage.value),
    per_page: String(perPage.value),
  })
  if (query.value) parameters.set('query', query.value)
  if (month.value) parameters.set('month', month.value)
  if (category.value) parameters.set('category', category.value)

  try {
    const response = await fetch(`${apiUrl('purchases')}?${parameters}`)
    if (!response.ok) throw new Error('無法載入消費紀錄')
    const data = await response.json() as PurchaseResponse
    records.value = data.items
    currentPage.value = data.page
    perPage.value = data.per_page
    total.value = data.total
    totalPages.value = data.total_pages
  } catch (error) {
    records.value = []
    errorMessage.value = error instanceof Error ? error.message : '載入資料時發生錯誤'
  } finally {
    loading.value = false
  }
}

async function updateRoute(page = 1): Promise<void> {
  await router.replace({
    name: 'purchases',
    query: {
      ...(query.value.trim() ? { query: query.value.trim() } : {}),
      ...(month.value ? { month: month.value } : {}),
      ...(category.value ? { category: category.value } : {}),
      ...(perPage.value !== 50 ? { per_page: String(perPage.value) } : {}),
      ...(page > 1 ? { page: String(page) } : {}),
    },
  })
}

async function clearSearch(): Promise<void> {
  query.value = ''
  month.value = ''
  category.value = ''
  perPage.value = 50
  await router.replace({ name: 'purchases' })
}

watch(
  () => route.query,
  async (routeQuery) => {
    query.value = routeText(routeQuery.query)
    month.value = routeText(routeQuery.month)
    category.value = routeText(routeQuery.category)
    perPage.value = [25, 50, 100].includes(routeNumber(routeQuery.per_page, 50))
      ? routeNumber(routeQuery.per_page, 50)
      : 50
    currentPage.value = routeNumber(routeQuery.page, 1)
    await loadRecords()
  },
  { immediate: true },
)

onMounted(async () => {
  try {
    await loadOptions()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '無法載入篩選選項'
  }
})
</script>

<template>
  <section class="card page-heading">
    <div>
      <p class="eyebrow">發票記帳助手 1.0</p>
      <h1>消費紀錄</h1>
      <p class="supporting-text">按月份瀏覽每筆消費，並可搜尋或進入單筆完整修正。</p>
    </div>

    <form class="filter-form purchase-filters" @submit.prevent="updateRoute(1)">
      <label class="query-field">
        <span>搜尋</span>
        <input v-model="query" type="search" placeholder="商品、商店、發票號碼或日期" autocomplete="off">
      </label>
      <label>
        <span>月份</span>
        <select v-model="month">
          <option value="">全部月份</option>
          <option v-for="item in months" :key="item" :value="item">{{ monthLabel(item) }}</option>
        </select>
      </label>
      <label>
        <span>分類</span>
        <select v-model="category">
          <option value="">全部分類</option>
          <option v-for="item in categories" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label>
        <span>每頁筆數</span>
        <select v-model.number="perPage">
          <option :value="25">25 筆</option>
          <option :value="50">50 筆</option>
          <option :value="100">100 筆</option>
        </select>
      </label>
      <button type="submit">搜尋</button>
      <button v-if="hasFilters" class="secondary-button" type="button" @click="clearSearch">清除</button>
    </form>
  </section>

  <section class="card">
    <div class="section-heading">
      <h2>明細</h2>
      <span v-if="!loading" class="result-count">共 {{ total }} 筆，第 {{ currentPage }}／{{ totalPages }} 頁</span>
    </div>

    <p v-if="errorMessage" class="message error-message" role="alert">{{ errorMessage }}</p>
    <p v-else-if="loading" class="message">正在載入消費紀錄…</p>

    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>日期</th>
            <th>發票號碼</th>
            <th>商品</th>
            <th>數量</th>
            <th>單價</th>
            <th>實付金額</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="(record, index) in records" :key="record.id">
            <tr v-if="startsMonthGroup(index)" class="month-divider">
              <td colspan="7">{{ record.month_label }}</td>
            </tr>
            <tr>
              <td>{{ record.date }}</td>
              <td>{{ record.invoice_number }}</td>
              <td>
                <RouterLink v-if="record.product_id" :to="{ name: 'product-detail', params: { id: record.product_id } }" :title="record.product_name">{{ shortName(record.product_name) }}</RouterLink>
                <span v-else :title="record.product_name">{{ shortName(record.product_name) }}</span>
                <small v-if="record.is_corrected" class="corrected-badge">已人工修正</small>
              </td>
              <td>{{ formatNumber(record.quantity) }}</td>
              <td>
                {{ formatMoney(record.unit_price) }}
                <small v-if="record.discount_amount < 0 && record.net_unit_price !== null" class="discount-note">折後 {{ formatMoney(record.net_unit_price) }}</small>
              </td>
              <td>
                {{ formatMoney(record.net_amount) }}
                <small v-if="record.discount_amount < 0" class="discount-note">折扣 {{ formatMoney(record.discount_amount) }}</small>
              </td>
              <td><RouterLink class="table-action" :to="{ name: 'purchase-edit', params: { id: record.id }, query: { returnTo: route.fullPath } }">修改</RouterLink></td>
            </tr>
          </template>
          <tr v-if="records.length === 0">
            <td colspan="7" class="empty-cell">找不到符合條件的消費紀錄。</td>
          </tr>
        </tbody>
      </table>
    </div>

    <nav v-if="totalPages > 1" class="pagination" aria-label="消費紀錄分頁">
      <button class="secondary-button" type="button" :disabled="currentPage <= 1" @click="updateRoute(currentPage - 1)">上一頁</button>
      <span>第 {{ currentPage }}／{{ totalPages }} 頁</span>
      <button class="secondary-button" type="button" :disabled="currentPage >= totalPages" @click="updateRoute(currentPage + 1)">下一頁</button>
    </nav>
  </section>
</template>
