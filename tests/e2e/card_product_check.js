const crypto = require("crypto");
const fs = require("fs");
const { chromium } = require("playwright");

const baseURL = process.env.TEST_BASE_URL;
const customerCookie = process.env.TEST_CUSTOMER_COOKIE;
const adminCookie = process.env.TEST_ADMIN_COOKIE;
const mailFile = process.env.TEST_MAIL_FILE;
const privateKeyFile = process.env.TEST_PRIVATE_KEY_FILE;
const adminScreenshot = process.env.TEST_ADMIN_SCREENSHOT;
const accountScreenshot = process.env.TEST_ACCOUNT_SCREENSHOT;
const mobileScreenshot = process.env.TEST_MOBILE_SCREENSHOT;
if (![baseURL, customerCookie, adminCookie, mailFile, privateKeyFile].every(Boolean)) {
  throw new Error("Missing card product E2E environment");
}

const primarySecret = "E2E-ACTIVATION-PRIMARY-1234";
const backupSecret = "E2E-ACTIVATION-BACKUP-5678";

function encryptedCallback(payment) {
  const payload = {
    merchantNo: payment.merchantNo,
    eventId: `card-event-${payment.merchantNo}`,
    transactionId: `card-txn-${payment.merchantNo}`,
    amount: Number(payment.amount),
    currency: payment.currency,
    status: "paid",
  };
  const plaintext = Buffer.from(JSON.stringify({ timestamp: Math.floor(Date.now() / 1000), payload }));
  const aesKey = crypto.randomBytes(32);
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", aesKey, iv);
  const encrypted = Buffer.concat([cipher.update(plaintext), cipher.final(), cipher.getAuthTag()]);
  const publicKey = crypto.createPublicKey(fs.readFileSync(privateKeyFile, "utf8"));
  const wrapped = crypto.publicEncrypt(
    { key: publicKey, padding: crypto.constants.RSA_PKCS1_OAEP_PADDING, oaepHash: "sha256" },
    aesKey,
  );
  return {
    alg: "RSA-OAEP-256+A256GCM",
    key: wrapped.toString("base64"),
    iv: iv.toString("base64"),
    data: encrypted.toString("base64"),
  };
}

function decodeMail() {
  const source = fs.readFileSync(mailFile, "utf8");
  const parts = source.split(/\r?\n\r?\n/);
  if (/Content-Transfer-Encoding:\s*base64/i.test(source) && parts[1]) {
    return Buffer.from(parts.slice(1).join("").replace(/\s/g, ""), "base64").toString("utf8");
  }
  return source;
}

async function clicdLastCreate() {
  const response = await fetch("http://127.0.0.1:19092/api/v1/test/last-create", {
    headers: { "X-API-Key": "token-b" },
  });
  if (!response.ok) throw new Error(`CLICD inspection failed: ${response.status}`);
  return (await response.json()).data;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const admin = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await admin.addCookies([{ name: "vps_session", value: adminCookie, url: baseURL }]);
    const adminPage = await admin.newPage();
    await adminPage.goto(`${baseURL}/admin/plans`, { waitUntil: "networkidle" });
    await adminPage.selectOption("#plan-product-type", "card");
    if (await adminPage.locator("#plan-cloud-fields").isVisible()) throw new Error("Cloud fields remain visible for a card product");
    if (await adminPage.locator("#plan-cloud-checks").isVisible()) throw new Error("Cloud network controls remain visible for a card product");
    if (!(await adminPage.locator("#plan-card-fields").isVisible())) throw new Error("Card fields did not become visible");
    if (await adminPage.locator('#plan-cloud-fields input[name="cpu"]').isEnabled()) throw new Error("Hidden cloud controls remain enabled");
    await adminPage.fill('input[name="name"]', "邮箱自动发卡");
    await adminPage.fill('input[name="slug"]', "e2e-auto-card");
    await adminPage.fill('input[name="description"]', "支付成功后自动发送激活码");
    await adminPage.fill('input[name="price_cents"]', "1500");
    await adminPage.fill('textarea[name="card_delivery_note"]', "请在官方兑换页激活，有效期 30 天");
    await adminPage.fill('textarea[name="card_inventory"]', `${primarySecret}\n${backupSecret}`);
    await adminPage.screenshot({ path: adminScreenshot, fullPage: true });
    await Promise.all([
      adminPage.waitForURL("**/admin/plans?cards_added=2&cards_skipped=0"),
      adminPage.getByRole("button", { name: "保存套餐" }).click(),
    ]);
    const cardPlan = adminPage.locator(".plan-list article").filter({ hasText: "e2e-auto-card" });
    if (!(await cardPlan.textContent()).includes("可用 2")) throw new Error("Imported card stock was not shown");
    if ((await adminPage.textContent("body")).includes(primarySecret)) throw new Error("Admin page leaked a full card secret");
    if (Object.keys(await clicdLastCreate()).length) throw new Error("Creating a card plan called CLICD container creation");

    const customer = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await customer.addCookies([{ name: "vps_session", value: customerCookie, url: baseURL }]);
    const page = await customer.newPage();
    await page.goto(`${baseURL}/#plans`, { waitUntil: "networkidle" });
    const storefrontCard = page.locator(".plan-card").filter({ hasText: "邮箱自动发卡" });
    const storefrontText = await storefrontCard.textContent();
    for (const value of ["自动发卡", "SMTP 自动交付", "剩余库存 2"]) {
      if (!storefrontText.includes(value)) throw new Error(`Storefront is missing: ${value}`);
    }
    await storefrontCard.locator('button[name="payment_method"][value="hashpay"]').click();
    await page.waitForURL("http://127.0.0.1:19094/pay/**");
    const paymentResponse = await fetch("http://127.0.0.1:19094/api/test/last");
    const payment = (await paymentResponse.json()).data;
    if (!payment.merchantNo?.startsWith("VP") || Number(payment.amount) !== 15) throw new Error("HashPay card checkout payload is wrong");
    const callback = await fetch(`${baseURL}/hashpay/callback`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-HashPay-Merchant": "mock-merchant" },
      body: JSON.stringify(encryptedCallback(payment)),
    });
    if (!callback.ok) throw new Error(`Card payment callback failed: ${callback.status} ${await callback.text()}`);

    await page.goto(`${baseURL}/account?payment=returned#orders`, { waitUntil: "networkidle" });
    const orderCard = page.locator(".account-order").filter({ hasText: payment.merchantNo });
    for (let attempt = 0; attempt < 30; attempt += 1) {
      if ((await orderCard.textContent()).includes("已完成") && fs.existsSync(mailFile)) break;
      await page.waitForTimeout(300);
      await page.reload({ waitUntil: "networkidle" });
    }
    const accountText = await orderCard.textContent();
    if (!accountText.includes("********1234") || !accountText.includes("已完成")) throw new Error("Delivered card summary is missing");
    if (accountText.includes(primarySecret) || (await page.textContent("body")).includes(primarySecret)) throw new Error("Customer page leaked a full card secret");
    if (await orderCard.locator(".refund-request").count()) throw new Error("Delivered card product exposed the cloud refund action");
    const mailText = decodeMail();
    if (!mailText.includes(primarySecret) || !mailText.includes("请在官方兑换页激活")) throw new Error("SMTP message is missing the full card or delivery note");
    await page.screenshot({ path: accountScreenshot, fullPage: true });

    await orderCard.getByRole("button", { name: "重新发送到注册邮箱" }).click();
    await page.waitForURL("**/account?card_mail=queued#orders");
    if (!(await page.textContent("body")).includes("重新进入发送队列")) throw new Error("Card resend confirmation is missing");
    if (Object.keys(await clicdLastCreate()).length) throw new Error("Paid card delivery called CLICD container creation");

    await adminPage.goto(`${baseURL}/admin/plans`, { waitUntil: "networkidle" });
    const deliveryRow = adminPage.locator("tr").filter({ hasText: payment.merchantNo });
    const deliveryText = await deliveryRow.textContent();
    if (!deliveryText.includes("********1234") || !deliveryText.includes("已完成")) throw new Error("Admin delivery record is incomplete");
    if ((await adminPage.textContent("body")).includes(primarySecret)) throw new Error("Admin delivery record leaked a full card secret");

    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 } });
    await mobile.addCookies([{ name: "vps_session", value: customerCookie, url: baseURL }]);
    const mobilePage = await mobile.newPage();
    await mobilePage.goto(`${baseURL}/account#orders`, { waitUntil: "networkidle" });
    const overflow = await mobilePage.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    if (overflow) throw new Error("Card account page has horizontal overflow on mobile");
    await mobilePage.screenshot({ path: mobileScreenshot, fullPage: true });

    console.log(JSON.stringify({
      merchantNo: payment.merchantNo,
      masked: "********1234",
      smtpContainsFullSecret: true,
      clicdCreatePayload: await clicdLastCreate(),
      mobileOverflow: overflow,
    }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
