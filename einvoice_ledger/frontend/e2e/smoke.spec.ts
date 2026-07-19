import { expect, test } from '@playwright/test'

test('Vue routes and API-relative navigation work after reload', async ({ page }) => {
  await page.goto('/#/dashboard')
  await expect(page.getByRole('heading', { name: '記帳總覽' })).toBeVisible()
  await page.getByRole('link', { name: '資料品質' }).click()
  await expect(page.getByRole('heading', { name: '資料品質中心' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '資料品質中心' })).toBeVisible()
  await page.getByRole('link', { name: '分類預算' }).click()
  await expect(page.getByRole('heading', { name: '分類預算' })).toBeVisible()
})
