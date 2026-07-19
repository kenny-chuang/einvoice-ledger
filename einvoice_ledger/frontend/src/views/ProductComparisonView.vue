<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiUrl } from '../lib/urls'

interface ProductComparison { id:number; name:string; purchase_count:number; minimum:number|null; maximum:number|null; latest:number|null; latest_date:string|null; distance_from_low:number|null; distance_from_target:number|null; alert_enabled:boolean }
interface ProductResult { items:ProductComparison[]; total:number; page:number; per_page:number; total_pages:number }

const route=useRoute(); const router=useRouter()
const products=ref<ProductComparison[]>([]); const categories=ref<string[]>([])
const query=ref(''); const category=ref(''); const alertStatus=ref(''); const view=ref('recent'); const sort=ref('recent'); const perPage=ref(25)
const total=ref(0); const currentPage=ref(1); const totalPages=ref(1); const loading=ref(false); const errorMessage=ref('')
const hasFilters=computed(()=>Boolean(query.value||category.value||alertStatus.value||view.value!=='recent'||sort.value!=='recent'||perPage.value!==25))
const routeValue=(value:unknown)=>typeof value==='string'?value:''
const formatMoney=(value:number|null)=>value===null?'-':`NT$${new Intl.NumberFormat('zh-TW',{maximumFractionDigits:3}).format(value)}`
const priceGapClass=(value:number|null)=>value===0?'price-low':value!==null&&value>0?'price-up':''

async function loadCategories(){const response=await fetch(apiUrl('categories')); if(!response.ok)throw new Error('無法載入分類選項'); categories.value=await response.json() as string[]}
async function loadProducts(){
  loading.value=true; errorMessage.value=''; const parameters=new URLSearchParams()
  if(query.value)parameters.set('query',query.value); if(category.value)parameters.set('category',category.value); if(alertStatus.value)parameters.set('alert_status',alertStatus.value)
  parameters.set('view',view.value); parameters.set('sort',sort.value); parameters.set('page',String(currentPage.value)); parameters.set('per_page',String(perPage.value))
  try{const response=await fetch(`${apiUrl('product-comparisons')}?${parameters}`); if(!response.ok)throw new Error('無法載入商品比價資料'); const result=await response.json() as ProductResult; products.value=result.items; total.value=result.total; currentPage.value=result.page; totalPages.value=result.total_pages}
  catch(error){products.value=[]; errorMessage.value=error instanceof Error?error.message:'載入資料時發生錯誤'}finally{loading.value=false}
}
async function updateRoute(page=1){await router.replace({name:'products',query:{...(query.value.trim()?{query:query.value.trim()}:{}),...(category.value?{category:category.value}:{}),...(alertStatus.value?{alert_status:alertStatus.value}:{}),...(view.value!=='recent'?{view:view.value}:{}),...(sort.value!=='recent'?{sort:sort.value}:{}),...(perPage.value!==25?{per_page:String(perPage.value)}:{}),...(page>1?{page:String(page)}:{})}})}
async function clearSearch(){query.value='';category.value='';alertStatus.value='';view.value='recent';sort.value='recent';perPage.value=25;await router.replace({name:'products'})}
function openProduct(id:number){router.push({name:'product-detail',params:{id}})}
watch(()=>route.query,async q=>{query.value=routeValue(q.query);category.value=routeValue(q.category);alertStatus.value=routeValue(q.alert_status);view.value=routeValue(q.view)||'recent';sort.value=routeValue(q.sort)||'recent';perPage.value=Number(routeValue(q.per_page))||25;currentPage.value=Number(routeValue(q.page))||1;await loadProducts()},{immediate:true})
onMounted(async()=>{try{await loadCategories()}catch(error){errorMessage.value=error instanceof Error?error.message:'無法載入分類選項'}})
</script>

<template>
  <section class="card page-heading"><div><p class="eyebrow">發票記帳助手 1.0</p><h1>商品比價</h1><p class="supporting-text">快速找出最近購買、經常購買及價格上漲的商品；價格只使用消費明細原始單價。</p></div>
    <form class="filter-form simple-product-filters" @submit.prevent="updateRoute(1)"><label><span>搜尋商品</span><input v-model="query" type="search" placeholder="商品名稱或別名" autocomplete="off"></label><label><span>分類</span><select v-model="category"><option value="">全部分類</option><option v-for="item in categories" :key="item">{{ item }}</option></select></label><label><span>排序</span><select v-model="sort"><option value="recent">最近購買</option><option value="count">消費次數</option><option value="price_gap">價差最大</option><option value="lowest">目前單價最低</option><option value="name">商品名稱</option></select></label><button>套用</button><button v-if="hasFilters" class="secondary-button" type="button" @click="clearSearch">清除</button></form>
  </section>
  <section class="card"><div class="section-heading"><h2>商品清單</h2><span v-if="!loading" class="result-count">共 {{ total }} 項，第 {{ currentPage }}／{{ totalPages }} 頁</span></div><p v-if="errorMessage" class="message error-message">{{ errorMessage }}</p><p v-else-if="loading" class="message">正在載入商品資料…</p>
    <div v-else class="table-wrap product-table-wrap"><table class="product-table"><thead><tr><th>商品</th><th>最近單價</th><th>歷史最低</th><th>價差</th><th>購買次數</th></tr></thead><tbody><tr v-for="product in products" :key="product.id" class="clickable-row" tabindex="0" @click="openProduct(product.id)" @keydown.enter="openProduct(product.id)"><td class="product-name-cell"><RouterLink :to="{name:'product-detail',params:{id:product.id}}" :title="product.name" @click.stop>{{ product.name }}</RouterLink></td><td class="latest-price">{{ formatMoney(product.latest) }}</td><td>{{ formatMoney(product.minimum) }}</td><td><span class="price-gap" :class="priceGapClass(product.distance_from_low)">{{ product.distance_from_low===0?'最低價':formatMoney(product.distance_from_low) }}</span></td><td>{{ product.purchase_count }} 次</td></tr><tr v-if="products.length===0"><td colspan="5" class="empty-cell">找不到符合條件的商品。</td></tr></tbody></table></div>
    <nav v-if="totalPages>1" class="pagination"><button class="secondary-button" :disabled="currentPage<=1" @click="updateRoute(currentPage-1)">上一頁</button><span>第 {{ currentPage }}／{{ totalPages }} 頁</span><button class="secondary-button" :disabled="currentPage>=totalPages" @click="updateRoute(currentPage+1)">下一頁</button></nav>
  </section>
</template>
