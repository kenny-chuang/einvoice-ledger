<script setup lang="ts">
</script>

<template>
  <header class="site-header">
    <div class="header-inner">
      <RouterLink class="brand" to="/dashboard">發票記帳助手</RouterLink>
      <nav aria-label="主要導覽">
        <RouterLink to="/dashboard">總覽</RouterLink>
        <RouterLink to="/purchases">消費紀錄</RouterLink>
        <RouterLink to="/products">商品比價</RouterLink>
        <RouterLink to="/settings">設定</RouterLink>
      </nav>
    </div>
  </header>
  <main>
    <RouterView />
  </main>
</template>
