const fetch = require('node-fetch');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

// Config via env
const BASE_URL = process.env.BASE_URL || 'http://localhost:3000';
const HASHPAY_MOCK = process.env.HASHPAY_MOCK || 'http://localhost:8081';
const SMTP_UI = process.env.SMTP_UI || 'http://localhost:5000';
const CLICD_BASE = process.env.CLICD_BASE || 'http://51.159.67.180:8999';
const CLICD_API_KEY = process.env.CLICD_API_KEY || '';

(async () => {
  const artifactsDir = path.resolve(process.cwd(), 'artifacts/full_flow');
  fs.mkdirSync(artifactsDir, { recursive: true });

  const orderId = `e2e-${Date.now()}`;
  const userEmail = `e2e+${orderId}@example.com`;
  const amount = 9.99;

  console.log('Simulating payment via HashPay-mock...');
  const simulateResp = await fetch(`${HASHPAY_MOCK}/simulate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ callback_url: `${BASE_URL}/api/payments/hashpay`, order_id: orderId, amount, user_email: userEmail }),
  });
  const simulateJson = await simulateResp.json();
  fs.writeFileSync(path.join(artifactsDir, 'simulate_response.json'), JSON.stringify(simulateJson, null, 2));
  console.log('simulate response saved');

  // wait for processing
  console.log('Waiting 3 seconds for webhook processing...');
  await new Promise((r) => setTimeout(r, 3000));

  // Query smtp4dev messages
  try {
    const msgsResp = await fetch(`${SMTP_UI}/api/Messages`);
    const msgs = await msgsResp.json();
    fs.writeFileSync(path.join(artifactsDir, 'smtp_messages.json'), JSON.stringify(msgs, null, 2));
    console.log('smtp messages saved, count=', msgs.length);
  } catch (e) {
    console.warn('Failed to fetch smtp4dev messages:', e.message);
  }

  // Query CLICD containers
  try {
    const containersResp = await fetch(`${CLICD_BASE}/api/v1/containers`, {
      headers: { Authorization: `Bearer ${CLICD_API_KEY}` },
    });
    const containers = await containersResp.json();
    fs.writeFileSync(path.join(artifactsDir, 'clicd_containers.json'), JSON.stringify(containers, null, 2));
    console.log('clicd containers saved');
    // find container by name
    let found = null;
    if (Array.isArray(containers)) {
      found = containers.find((c) => c.name === `order-${orderId}` || (c.metadata && c.metadata.order_id === orderId));
    } else if (containers && containers.data) {
      found = containers.data.find((c) => c.name === `order-${orderId}`);
    }
    if (found) {
      fs.writeFileSync(path.join(artifactsDir, 'created_container.json'), JSON.stringify(found, null, 2));
      console.log('Found created container:', found.id || found.name);
    } else {
      console.log('No matching container found yet');
    }
  } catch (e) {
    console.warn('Failed to query CLICD:', e.message);
  }

  // Use Playwright to screenshot app root, smtp4dev UI, and a simple CLICD JSON view
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    console.log('Opening app root for screenshot...');
    await page.goto(BASE_URL, { waitUntil: 'networkidle' });
    await page.screenshot({ path: path.join(artifactsDir, 'app_root.png'), fullPage: true });

    console.log('Opening smtp4dev UI for screenshot...');
    await page.goto(SMTP_UI, { waitUntil: 'networkidle' });
    // try to open messages list if available
    await page.screenshot({ path: path.join(artifactsDir, 'smtp_ui.png'), fullPage: true });

    // render saved CLICD containers JSON as HTML and screenshot
    const clicdJsonPath = path.join(artifactsDir, 'clicd_containers.json');
    if (fs.existsSync(clicdJsonPath)) {
      const json = fs.readFileSync(clicdJsonPath, 'utf8');
      await page.setContent(`<pre>${json.replace(/</g, '&lt;')}</pre>`);
      await page.screenshot({ path: path.join(artifactsDir, 'clicd_containers.png'), fullPage: true });
    }

  } catch (e) {
    console.error('Playwright error:', e.message);
  } finally {
    await browser.close();
  }

  console.log('Full flow artifacts saved to', artifactsDir);
})();
