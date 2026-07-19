<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiUrl } from '../lib/urls'

interface CategoryUsage {
  name: string
  product_count: number
  deletable: boolean
}

interface StatusResponse {
  login_required: boolean
  has_session: boolean
  session_valid: boolean
  last_run: { status: string; message: string; finished_at: string | null } | null
}

interface SystemStatus {
  version: string
  database_version: string | null
  diagnostic_retention_days: number
  mqtt: { configured: boolean; connected: boolean; error: string; host: string; port: number }
  backups: { name: string; size: number }[]
}

interface CategoryRule {
  id: number
  rule_type: string
  pattern: string
  category: string
  priority: number
}

interface BuiltInRule {
  category: string
  keywords: string[]
}

const categories = ref<string[]>([])
const usages = ref<CategoryUsage[]>([])
const replacements = ref<Record<string, string>>({})
const budgetPolicies = ref<Record<string, string>>({})
const status = ref<StatusResponse | null>(null)
const systemStatus = ref<SystemStatus | null>(null)
const categoryRules = ref<CategoryRule[]>([])
const builtInRules = ref<BuiltInRule[]>([])
const ruleKeyword = ref('')
const ruleCategory = ref('')
const ruleNewCategory = ref('')
const rulePriority = ref(100)
const ruleApplyExisting = ref(true)
const ruleSaving = ref(false)
const ruleMessage = ref('')
const ruleError = ref('')
const loading = ref(true)
const categoryMessage = ref('')
const categoryError = ref('')
const deletingCategory = ref('')
const previewImage = ref('')
const previewLoading = ref(false)
const loginLoading = ref(false)
const loginMessage = ref('')
const loginError = ref('')
const carrierIdentifier = ref('')
const password = ref('')
const captcha = ref('')
const challengeToken = ref('')
const securityVerification = ref(false)

function formatDate(value: string | null): string {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-TW', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

async function loadSettings(): Promise<void> {
  loading.value = true
  try {
    const [usageResponse, statusResponse, systemResponse, rulesResponse] = await Promise.all([
      fetch(apiUrl('categories/usage')),
      fetch(apiUrl('status')),
      fetch(apiUrl('system')),
      fetch(apiUrl('rules?rule_type=item_keyword')),
    ])
    if (!usageResponse.ok || !statusResponse.ok || !systemResponse.ok || !rulesResponse.ok) throw new Error('無法載入管理資料')
    usages.value = await usageResponse.json() as CategoryUsage[]
    categories.value = usages.value.map((usage) => usage.name)
    const rulesResult = await rulesResponse.json() as { items: CategoryRule[]; built_in: BuiltInRule[] }
    categoryRules.value = rulesResult.items
    builtInRules.value = rulesResult.built_in
    if (!ruleCategory.value || !categories.value.includes(ruleCategory.value)) {
      ruleCategory.value = categories.value.find((item) => item === '待分類') ?? categories.value[0] ?? ''
    }
    status.value = await statusResponse.json() as StatusResponse
    systemStatus.value = await systemResponse.json() as SystemStatus
    const nextReplacements: Record<string, string> = {}
    for (const usage of usages.value) {
      if (usage.deletable) {
        nextReplacements[usage.name] = usage.name === '餐費' && categories.value.includes('餐點')
          ? '餐點'
          : categories.value.find((category) => category === '待分類') ?? categories.value.find((category) => category !== usage.name) ?? ''
      }
      budgetPolicies.value[usage.name] = budgetPolicies.value[usage.name] ?? 'keep_target'
    }
    replacements.value = nextReplacements
  } catch (error) {
    categoryError.value = error instanceof Error ? error.message : '載入管理資料時發生錯誤'
  } finally {
    loading.value = false
  }
}

async function saveCategoryRule(): Promise<void> {
  const targetCategory = ruleNewCategory.value.trim() || ruleCategory.value
  if (!ruleKeyword.value.trim() || !targetCategory) {
    ruleError.value = '請輸入關鍵字並選擇分類'
    return
  }
  ruleSaving.value = true
  ruleMessage.value = ''
  ruleError.value = ''
  const form = new FormData()
  form.append('rule_type', 'item_keyword')
  form.append('pattern', ruleKeyword.value.trim())
  form.append('category', targetCategory)
  form.append('priority', String(rulePriority.value))
  form.append('apply_existing', ruleApplyExisting.value ? 'true' : 'false')
  try {
    const response = await fetch(apiUrl('rules'), { method: 'POST', body: form })
    const result = await response.json() as { updated_products?: number; detail?: string }
    if (!response.ok) throw new Error(result.detail ?? '新增規則失敗')
    ruleMessage.value = `已新增「${ruleKeyword.value.trim()} → ${targetCategory}」，並更新 ${result.updated_products ?? 0} 個待分類商品`
    ruleKeyword.value = ''
    ruleNewCategory.value = ''
    await loadSettings()
  } catch (error) {
    ruleError.value = error instanceof Error ? error.message : '新增規則失敗'
  } finally {
    ruleSaving.value = false
  }
}

async function removeCategoryRule(rule: CategoryRule): Promise<void> {
  if (!window.confirm(`確定刪除「${rule.pattern} → ${rule.category}」規則？既有商品分類不會被還原。`)) return
  ruleMessage.value = ''
  ruleError.value = ''
  try {
    const response = await fetch(apiUrl(`rules/${rule.id}`), { method: 'DELETE' })
    const result = await response.json() as { detail?: string }
    if (!response.ok) throw new Error(result.detail ?? '刪除規則失敗')
    ruleMessage.value = `已刪除「${rule.pattern}」規則`
    await loadSettings()
  } catch (error) {
    ruleError.value = error instanceof Error ? error.message : '刪除規則失敗'
  }
}

async function requestCategoryDelete(category: string, replacement: string, confirmed: boolean): Promise<Response> {
  const form = new FormData()
  form.append('category', category)
  form.append('replacement', replacement)
  form.append('confirmed', confirmed ? 'true' : 'false')
  form.append('budget_policy', budgetPolicies.value[category] ?? 'keep_target')
  return fetch(apiUrl('categories/delete'), { method: 'POST', body: form })
}

async function deleteCategory(usage: CategoryUsage): Promise<void> {
  const replacement = replacements.value[usage.name]
  if (!replacement) {
    categoryError.value = '請先選擇商品要移入的分類'
    return
  }
  deletingCategory.value = usage.name
  categoryError.value = ''
  categoryMessage.value = ''
  try {
    let response = await requestCategoryDelete(usage.name, replacement, false)
    let result = await response.json() as { moved_products?: number; detail?: string | { message?: string } }
    if (response.status === 409) {
      const message = typeof result.detail === 'string' ? result.detail : result.detail?.message
      if (!window.confirm(message ?? `確定刪除「${usage.name}」嗎？`)) return
      response = await requestCategoryDelete(usage.name, replacement, true)
      result = await response.json() as typeof result
    }
    if (!response.ok) {
      const message = typeof result.detail === 'string' ? result.detail : result.detail?.message
      throw new Error(message ?? '分類刪除失敗')
    }
    categoryMessage.value = `已刪除「${usage.name}」，${result.moved_products ?? 0} 個商品已移到「${replacement}」`
    await loadSettings()
  } catch (error) {
    categoryError.value = error instanceof Error ? error.message : '分類刪除失敗'
  } finally {
    deletingCategory.value = ''
  }
}

async function loadLoginPreview(): Promise<void> {
  previewLoading.value = true
  loginError.value = ''
  loginMessage.value = ''
  try {
    const response = await fetch(apiUrl('auth/login-preview'))
    const result = await response.json() as { image?: string; challenge_token?: string; captcha_guess?: string; security_verification?: boolean; detail?: string }
    if (!response.ok || !result.image) throw new Error(result.detail ?? '無法載入驗證碼畫面')
    previewImage.value = result.image
    challengeToken.value = result.challenge_token ?? ''
    captcha.value = result.captcha_guess ?? ''
    securityVerification.value = result.security_verification ?? false
  } catch (error) {
    previewImage.value = ''
    challengeToken.value = ''
    securityVerification.value = false
    loginError.value = error instanceof Error ? error.message : '無法載入驗證碼畫面'
  } finally {
    previewLoading.value = false
  }
}

async function interactWithLogin(event: MouseEvent): Promise<void> {
  if (!securityVerification.value || !challengeToken.value) return
  const image = event.currentTarget as HTMLImageElement
  const rect = image.getBoundingClientRect()
  const form = new FormData()
  form.append('challenge_token', challengeToken.value)
  form.append('x', String((event.clientX - rect.left) * image.naturalWidth / rect.width))
  form.append('y', String((event.clientY - rect.top) * image.naturalHeight / rect.height))
  previewLoading.value = true
  loginError.value = ''
  try {
    const response = await fetch(apiUrl('auth/login-interact'), { method: 'POST', body: form })
    const result = await response.json() as { image?: string; captcha_guess?: string; security_verification?: boolean; detail?: string }
    if (!response.ok || !result.image) throw new Error(result.detail ?? '無法更新登入畫面')
    previewImage.value = result.image
    captcha.value = result.captcha_guess ?? ''
    securityVerification.value = result.security_verification ?? false
  } catch (error) {
    loginError.value = error instanceof Error ? error.message : '安全性驗證操作失敗'
  } finally {
    previewLoading.value = false
  }
}

async function submitLogin(): Promise<void> {
  loginLoading.value = true
  loginError.value = ''
  loginMessage.value = ''
  const form = new FormData()
  form.append('challenge_token', challengeToken.value)
  form.append('carrier_identifier', carrierIdentifier.value)
  form.append('password', password.value)
  form.append('captcha', captcha.value)
  try {
    const response = await fetch(apiUrl('auth/login'), { method: 'POST', body: form })
    const result = await response.json() as { message?: string; detail?: string }
    password.value = ''
    captcha.value = ''
    if (!response.ok) throw new Error(result.detail ?? '登入未成功')
    loginMessage.value = result.message ?? '登入工作階段已更新'
    previewImage.value = ''
    challengeToken.value = ''
    await loadSettings()
  } catch (error) {
    password.value = ''
    const message = error instanceof Error ? error.message : '登入未成功'
    await loadLoginPreview()
    loginError.value = message
  } finally {
    loginLoading.value = false
  }
}

onMounted(loadSettings)
</script>

<template>
  <section class="page-intro"><div><p class="eyebrow">系統管理</p><h1>管理設定</h1><p class="supporting-text">管理商品分類，以及更新財政部登入工作階段。</p></div></section>

  <section class="card">
    <div class="section-heading"><h2>分類管理</h2><span class="result-count">刪除前會確認商品轉移位置</span></div>
    <p class="supporting-text">系統預設分類不能刪除；其他分類若仍有商品，確認後會先將商品移到指定分類。</p>
    <p v-if="categoryMessage" class="message success-message">{{ categoryMessage }}</p>
    <p v-else-if="categoryError" class="message error-message" role="alert">{{ categoryError }}</p>
    <p v-if="loading" class="message">正在載入分類…</p>
    <div v-else class="table-wrap"><table><thead><tr><th>分類</th><th>商品數</th><th>刪除後移至</th><th>預算衝突處理</th><th>操作</th></tr></thead><tbody>
      <tr v-for="usage in usages" :key="usage.name"><td>{{ usage.name }}</td><td>{{ usage.product_count }}</td><td><select v-if="usage.deletable" v-model="replacements[usage.name]" :aria-label="`刪除 ${usage.name} 後移至`"><option v-for="category in categories.filter((item) => item !== usage.name)" :key="category" :value="category">{{ category }}</option></select><span v-else>-</span></td><td><select v-if="usage.deletable" v-model="budgetPolicies[usage.name]"><option value="keep_target">保留目標預算</option><option value="sum">加總兩筆預算</option></select><span v-else>-</span></td><td><button v-if="usage.deletable" class="danger-button compact-button" type="button" :disabled="deletingCategory === usage.name" @click="deleteCategory(usage)">刪除</button><span v-else class="result-count">系統分類</span></td></tr>
    </tbody></table></div>
  </section>

  <section class="card">
    <div class="section-heading"><h2>品名分類規則</h2><span class="result-count">數字越小越優先</span></div>
    <p class="supporting-text">CSV 匯入時，只要商品名稱包含關鍵字，就會自動套用指定分類。新增規則可選擇同步整理目前仍為「待分類」的商品；刪除規則不會改回既有商品。</p>
    <div class="message"><strong>內建規則：</strong><span v-for="rule in builtInRules" :key="rule.category"> {{ rule.keywords.join('、') }} → {{ rule.category }}；</span></div>
    <p v-if="ruleMessage" class="message success-message">{{ ruleMessage }}</p>
    <p v-else-if="ruleError" class="message error-message" role="alert">{{ ruleError }}</p>
    <form class="filter-form" @submit.prevent="saveCategoryRule">
      <label><span>品名關鍵字</span><input v-model="ruleKeyword" placeholder="例如：果汁" required></label>
      <label><span>套用分類</span><select v-model="ruleCategory"><option v-for="item in categories" :key="item" :value="item">{{ item }}</option></select></label>
      <label><span>或新增分類</span><input v-model="ruleNewCategory" placeholder="留空則使用選單"></label>
      <label><span>優先順序</span><input v-model.number="rulePriority" type="number" min="0" max="1000" required></label>
      <label class="checkbox-label"><input v-model="ruleApplyExisting" type="checkbox"><span>套用到既有待分類商品</span></label>
      <button type="submit" :disabled="ruleSaving">{{ ruleSaving ? '新增中…' : '新增規則' }}</button>
    </form>
    <div class="table-wrap"><table><thead><tr><th>關鍵字</th><th>分類</th><th>優先順序</th><th>操作</th></tr></thead><tbody>
      <tr v-for="rule in categoryRules" :key="rule.id"><td>{{ rule.pattern }}</td><td>{{ rule.category }}</td><td>{{ rule.priority }}</td><td><button class="danger-button compact-button" type="button" @click="removeCategoryRule(rule)">刪除</button></td></tr>
      <tr v-if="categoryRules.length === 0"><td colspan="4" class="empty-cell">尚未新增自訂品名規則。</td></tr>
    </tbody></table></div>
  </section>

  <details v-if="systemStatus" class="card advanced-settings">
    <summary><strong>進階系統設定</strong><span class="result-count">版本、MQTT、備份與診斷</span></summary>
    <div class="section-heading advanced-heading"><h2>系統診斷</h2><span class="status-pill" :class="systemStatus.mqtt.connected ? 'allocated' : 'unallocated'">MQTT {{ systemStatus.mqtt.connected ? '已連線' : '未連線' }}</span></div>
    <div class="metrics"><div><small>應用版本</small><strong>{{ systemStatus.version }}</strong></div><div><small>資料庫版本</small><strong>{{ systemStatus.database_version ?? '未建立' }}</strong></div><div><small>診斷保留</small><strong>{{ systemStatus.diagnostic_retention_days }} 天</strong></div><div><small>備份</small><strong>{{ systemStatus.backups.length }} 份</strong></div></div>
    <p class="supporting-text">{{ systemStatus.mqtt.configured ? `${systemStatus.mqtt.host}:${systemStatus.mqtt.port}` : '尚未設定 MQTT Broker；Web App 仍可完整使用。請在 Home Assistant App 設定 mqtt_host，或用 MQTT_HOST 環境變數。' }}</p>
    <p v-if="systemStatus.mqtt.error" class="message">MQTT：{{ systemStatus.mqtt.error }}</p>
    <ul v-if="systemStatus.backups.length" class="allocation-list"><li v-for="backup in systemStatus.backups" :key="backup.name">{{ backup.name }}（{{ Math.round(backup.size / 1024) }} KiB）</li></ul>
  </details>

  <section id="login" class="card settings-login">
    <div class="section-heading"><h2>財政部登入續期</h2><span v-if="status" class="status-pill" :class="status.login_required ? 'unallocated' : 'allocated'">{{ status.login_required ? '需要重新登入' : '工作階段正常' }}</span><span v-else class="status-pill">檢查中…</span></div>
    <p class="supporting-text">ddddocr 只在本機辨識並預填圖形驗證碼，送出前請核對。請輸入申請手機條碼時登記的 10 碼手機號碼，不是「/」開頭的手機條碼；手機號碼與密碼不會寫入資料庫、日誌或瀏覽器儲存空間。</p>
    <div v-if="status?.last_run" class="sync-detail"><strong>{{ status.last_run.message }}</strong><small v-if="status.last_run.finished_at">{{ formatDate(status.last_run.finished_at) }}</small></div>
    <p v-if="loginMessage" class="message success-message">{{ loginMessage }}</p>
    <p v-else-if="loginError" class="message error-message" role="alert">{{ loginError }}</p>

    <button v-if="!previewImage" type="button" :disabled="previewLoading" @click="loadLoginPreview">{{ previewLoading ? '載入中…' : '載入登入畫面與驗證碼' }}</button>
    <template v-else>
      <p v-if="securityVerification" class="message">請直接點擊下方畫面中的「Verify you are human」；系統會把這次點擊送到短效瀏覽器。</p>
      <img class="login-preview" :class="{ 'interactive-preview': securityVerification }" :src="previewImage" alt="財政部登入頁面與圖形驗證碼" @click="interactWithLogin">
      <button class="secondary-button" type="button" :disabled="previewLoading" @click="loadLoginPreview">更新驗證碼畫面</button>
      <form v-if="!securityVerification" class="login-form" @submit.prevent="submitLogin">
        <label><span>手機號碼</span><input v-model="carrierIdentifier" type="tel" inputmode="numeric" pattern="09[0-9]{8}" maxlength="10" placeholder="09xxxxxxxx" required autocomplete="username"><small>申請手機條碼時登記的 10 碼手機號碼</small></label>
        <label><span>驗證碼（密碼）</span><input v-model="password" type="password" required autocomplete="current-password"></label>
        <label><span>圖形驗證碼</span><input v-model="captcha" required autocomplete="off"><small>本機 OCR 預填，辨識錯誤時可直接修改。</small></label>
        <button type="submit" :disabled="loginLoading">{{ loginLoading ? '登入中…' : '登入並保存工作階段' }}</button>
      </form>
    </template>
  </section>
</template>
