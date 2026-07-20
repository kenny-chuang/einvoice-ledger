import { expect, test } from '@playwright/test'

test('Vue routes and API-relative navigation work after reload', async ({ page }) => {
  await page.goto('/#/dashboard')
  await expect(page.getByRole('heading', { name: '記帳總覽' })).toBeVisible()
  await page.getByRole('link', { name: '消費紀錄' }).click()
  await expect(page.getByRole('heading', { name: '消費紀錄' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '消費紀錄' })).toBeVisible()
  await page.getByRole('link', { name: '設定' }).click()
  await expect(page.getByRole('heading', { name: '管理設定' })).toBeVisible()
})
