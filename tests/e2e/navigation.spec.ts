import { expect, test } from '@playwright/test';

const pages = [
  { path: '/', current: 'Overview' },
  { path: '/transactions', current: 'Transactions' },
  { path: '/balances', current: 'Balances & Net Worth' },
  { path: '/accounts', current: 'Accounts' },
  { path: '/attention', current: 'Attention Needed' },
  { path: '/rules', current: 'Categories & Rules' },
];

test('shared navigation stays aligned and marks the current page', async ({ page }) => {
  let expectedX: number | undefined;
  let expectedWidth: number | undefined;

  for (const destination of pages) {
    await page.goto(destination.path);
    const navigation = page.getByRole('navigation', { name: 'Main navigation' });
    await expect(navigation).toBeVisible();
    await expect(navigation.getByRole('link')).toHaveCount(6);
    await expect(navigation.getByRole('link', { name: destination.current })).toHaveAttribute('aria-current', 'page');

    const bounds = await navigation.boundingBox();
    expect(bounds).not.toBeNull();
    if (!bounds) continue;
    if (expectedX === undefined) {
      expectedX = bounds.x;
      expectedWidth = bounds.width;
    } else {
      expect(Math.abs(bounds.x - expectedX), `nav left edge on ${destination.path}`).toBeLessThanOrEqual(1);
      expect(Math.abs(bounds.width - expectedWidth!), `nav width on ${destination.path}`).toBeLessThanOrEqual(1);
    }
  }
});

test('Report a Problem stays available in the shared page footer', async ({ page }) => {
  for (const destination of pages) {
    await page.goto(destination.path);
    await expect(page.locator('.site-footer').getByRole('link', { name: 'Report a Problem' })).toBeVisible();
  }
});
