import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import ProductComparisonView from './views/ProductComparisonView.vue'
import PurchaseRecordsView from './views/PurchaseRecordsView.vue'
import ProductDetailView from './views/ProductDetailView.vue'
import DashboardView from './views/DashboardView.vue'
import DiscountsView from './views/DiscountsView.vue'
import PurchaseEditView from './views/PurchaseEditView.vue'
import SettingsView from './views/SettingsView.vue'
import BudgetsView from './views/BudgetsView.vue'
import QualityView from './views/QualityView.vue'
import './styles.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: DashboardView },
    { path: '/discounts', name: 'discounts', component: DiscountsView },
    { path: '/settings', name: 'settings', component: SettingsView },
    { path: '/budgets', name: 'budgets', component: BudgetsView },
    { path: '/quality', name: 'quality', component: QualityView },
    { path: '/purchases', name: 'purchases', component: PurchaseRecordsView },
    { path: '/purchases/:id(\\d+)/edit', name: 'purchase-edit', component: PurchaseEditView },
    { path: '/products', name: 'products', component: ProductComparisonView },
    { path: '/products/:id(\\d+)', name: 'product-detail', component: ProductDetailView },
  ],
})

createApp(App).use(router).mount('#app')
