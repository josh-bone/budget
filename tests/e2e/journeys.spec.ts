import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

const databasePath = '/tmp/budget-ui-playwright.sqlite3';

function seed(data: {
  accountName: string;
  accountId: string;
  transactions?: Array<{ id: string; date: string; amount: number; description: string }>;
  categories?: string[];
}): number[] {
  const script = String.raw`
import json, sqlite3, sys
data = json.loads(sys.argv[1])
db = sqlite3.connect(${JSON.stringify(databasePath)})
db.execute("PRAGMA foreign_keys=ON")
db.execute("INSERT INTO accounts (id, name) VALUES (?, ?)", (data["accountId"], data["accountName"]))
category_ids = []
for name in data.get("categories", []):
    cursor = db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    category_ids.append(cursor.lastrowid)
for transaction in data.get("transactions", []):
    db.execute("INSERT INTO transactions (account_id, transaction_id, posted_date, amount, description) VALUES (?, ?, ?, ?, ?)", (data["accountId"], transaction["id"], transaction["date"], transaction["amount"], transaction["description"]))
db.commit()
print(json.dumps(category_ids))
db.close()
`;
  const result = execFileSync('uv', ['run', '--no-sync', 'python', '-c', script, JSON.stringify(data)], {
    encoding: 'utf8',
    env: { ...process.env, BUDGET_DB_PATH: databasePath, UV_CACHE_DIR: '/tmp/uv-cache' },
  });
  return JSON.parse(result) as number[];
}

function accountData(label: string) {
  const id = randomUUID();
  return { id, name: `${label} ${id.slice(0, 8)}` };
}

test('monthly overview opens the matching transaction activity', async ({ page }) => {
  const account = accountData('Overview account');
  const description = `Overview purchase ${randomUUID()}`;
  seed({
    accountId: account.id,
    accountName: account.name,
    transactions: [
      { id: randomUUID(), date: '2026-02-12', amount: -42.5, description },
      { id: randomUUID(), date: '2026-02-01', amount: 2500, description: `Paycheck ${randomUUID()}` },
    ],
  });

  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Budget Overview' })).toBeVisible();
  const monthLink = page.getByRole('link', { name: '2026-02' });
  await expect(monthLink).toBeVisible();
  await monthLink.click();

  await expect(page).toHaveURL(/\/transactions\?month=2026-02/);
  await expect(page.getByText(description)).toBeVisible();
  await expect(page.getByText('Total spend: $42.50')).toBeVisible();
});

test('a manual category change is saved and can be reset to automatic', async ({ page }) => {
  const account = accountData('Category account');
  const description = `Unusual purchase ${randomUUID()}`;
  const [categoryId] = seed({
    accountId: account.id,
    accountName: account.name,
    categories: [`Test category ${randomUUID()}`],
    transactions: [{ id: randomUUID(), date: '2026-03-10', amount: -18, description }],
  });
  await page.goto('/transactions');
  const row = page.getByRole('row').filter({ hasText: description });
  await row.getByLabel(`Category for ${description}`).selectOption(String(categoryId));
  await expect(row.getByText('Manual')).toBeVisible();
  await expect(row.getByRole('button', { name: 'Reset to Auto' })).toBeVisible();
  await row.getByRole('button', { name: 'Reset to Auto' }).click();
  await expect(row.getByText('Manual')).toHaveCount(0);
});

test('creating a description rule categorizes matching transactions', async ({ page }) => {
  const account = accountData('Rules account');
  const merchant = `Corner Market ${randomUUID()}`;
  const description = `${merchant} #123`;
  const [categoryId] = seed({
    accountId: account.id,
    accountName: account.name,
    categories: [`Rule category ${randomUUID()}`],
    transactions: [{ id: randomUUID(), date: '2026-04-06', amount: -27, description }],
  });
  await page.goto(`/rules?category_id=${categoryId}`);
  const addRule = page.locator('form.rule-add');
  await addRule.getByLabel('Rule name').fill('Market purchases');
  await addRule.getByLabel('Criterion').selectOption('description');
  await addRule.getByLabel('Description', { exact: true }).fill(merchant);
  await addRule.getByRole('button', { name: 'Add rule' }).click();
  await expect(page.getByRole('status')).toContainText('Rule saved.');

  await page.goto('/transactions');
  const row = page.getByRole('row').filter({ hasText: description });
  await expect(row.getByLabel(`Category for ${description}`)).toHaveValue(String(categoryId));
  await expect(row.getByText('Manual')).toHaveCount(0);
});

test('a newly created account can be assigned an owner', async ({ page }) => {
  const account = accountData('New account');
  const owner = `Household ${randomUUID().slice(0, 8)}`;

  await page.goto('/accounts');
  await page.getByLabel('Add a canonical account').fill(account.name);
  await page.getByRole('button', { name: 'Add account' }).click();
  await expect(page.getByRole('status')).toContainText(`Added account ${account.name}.`);

  const row = page.getByRole('row').filter({ hasText: account.name });
  await row.getByLabel(`Owner for ${account.name}`).fill(owner);
  await row.getByRole('button', { name: 'Save' }).first().click();
  await expect(row.getByLabel(`Owner for ${account.name}`)).toHaveValue(owner);
});
