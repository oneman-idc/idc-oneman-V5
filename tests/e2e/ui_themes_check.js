const path = require("path");
const { chromium } = require("playwright");

const baseURL = process.env.TEST_BASE_URL || "http://127.0.0.1:19080";
const customerCookie = process.env.TEST_CUSTOMER_COOKIE;
const adminCookie = process.env.TEST_ADMIN_COOKIE;
if (!customerCookie || !adminCookie) throw new Error("TEST_CUSTOMER_COOKIE and TEST_ADMIN_COOKIE are required");

const screenshots = {
  dashboard: process.env.TEST_DEFAULT_SCREENSHOT || path.resolve("..", "ui-default-dashboard.png"),
  newskin: process.env.TEST_NEWSKIN_SCREENSHOT || path.resolve("..", "ui-newskin-dark.png"),
  glass: process.env.TEST_GLASS_SCREENSHOT || path.resolve("..", "ui-glass-mobile.png"),
};

async function assertPreferences(page, url) {
  await page.goto(`${baseURL}${url}`, { waitUntil: "networkidle" });
  const trigger = page.locator(".ui-settings-trigger");
  if (await trigger.count() !== 1 || !(await trigger.isVisible())) throw new Error(`Missing interface control on ${url}`);
  const box = await trigger.boundingBox();
  const viewport = page.viewportSize();
  if (!box || box.x < viewport.width / 2 || box.x + box.width > viewport.width) throw new Error(`Interface control is not at the top right on ${url}`);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  if (overflow) throw new Error(`Horizontal overflow on ${url}`);
  return page.textContent("body");
}

async function openPreferences(page) {
  const trigger = page.locator(".ui-settings-trigger");
  await trigger.click();
  const panel = page.locator(".ui-preferences-panel");
  if (!(await panel.isVisible())) throw new Error("Interface preferences panel did not open");
  return panel;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const pageErrors = [];
  try {
    const publicContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const publicPage = await publicContext.newPage();
    publicPage.on("pageerror", (error) => pageErrors.push(error.message));
    await publicPage.goto(`${baseURL}/`, { waitUntil: "networkidle" });
    await publicPage.evaluate(() => localStorage.removeItem("vps-one-ui"));
    await publicPage.reload({ waitUntil: "networkidle" });
    let state = await publicPage.evaluate(() => window.VPS_UI.getState());
    if (state.skin !== "dashboard" || state.language !== "zh-CN") throw new Error(`Unexpected defaults: ${JSON.stringify(state)}`);
    await publicPage.evaluate(() => window.VPS_UI.setPreference("mode", "light"));
    await publicPage.screenshot({ path: screenshots.dashboard, fullPage: true });
    for (const route of ["/", "/login", "/register"]) await assertPreferences(publicPage, route);
    await publicPage.goto(`${baseURL}/`, { waitUntil: "networkidle" });
    await publicPage.evaluate(() => window.VPS_UI.setPreference("language", "en"));
    const publicText = await publicPage.textContent("body");
    for (const label of ["Every server is built to perform", "Product plans", "GB high-speed disk", "GB traffic", "/item"]) {
      if (!publicText.includes(label)) throw new Error(`Storefront English translation is missing: ${label}`);
    }

    const customer = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await customer.addCookies([{ name: "vps_session", value: customerCookie, url: baseURL }]);
    const customerPage = await customer.newPage();
    customerPage.on("pageerror", (error) => pageErrors.push(error.message));
    await customerPage.goto(`${baseURL}/dashboard`, { waitUntil: "networkidle" });
    let panel = await openPreferences(customerPage);
    await panel.locator('[data-ui-setting="skin"][data-ui-value="newskin"]').click();
    await panel.locator('[data-ui-setting="mode"][data-ui-value="dark"]').click();
    await panel.locator('[data-ui-setting="language"][data-ui-value="en"]').click();
    state = await customerPage.evaluate(() => window.VPS_UI.getState());
    if (state.skin !== "newskin" || state.mode !== "dark" || state.color !== "dark" || state.language !== "en") {
      throw new Error(`NEW SKIN state did not apply: ${JSON.stringify(state)}`);
    }
    const dashboardText = await customerPage.textContent("body");
    for (const label of ["My cloud servers", "Buy products", "Server management"]) {
      if (!dashboardText.includes(label)) throw new Error(`Dashboard English translation is missing: ${label}`);
    }
    await customerPage.locator(".ui-panel-close").click();
    await customerPage.screenshot({ path: screenshots.newskin, fullPage: true });
    const accountText = await assertPreferences(customerPage, "/account");
    for (const label of ["Wallet balance", "Orders", "Top-up history"]) {
      if (!accountText.includes(label)) throw new Error(`Account English translation is missing: ${label}`);
    }
    state = await customerPage.evaluate(() => window.VPS_UI.getState());
    if (state.skin !== "newskin" || state.language !== "en") throw new Error("Preferences did not persist across customer pages");

    const admin = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await admin.addCookies([{ name: "vps_session", value: adminCookie, url: baseURL }]);
    await admin.addInitScript(() => localStorage.setItem("vps-one-ui", JSON.stringify({ skin: "newskin", mode: "dark", language: "en" })));
    const adminPage = await admin.newPage();
    adminPage.on("pageerror", (error) => pageErrors.push(error.message));
    const adminRoutes = [
      ["/admin", ["Overview", "Latest orders", "Job queue"]],
      ["/admin/refunds", ["Refund review", "User / order"]],
      ["/admin/products", ["Product control", "Container management", "Disk", "Reset password"]],
      ["/admin/plans", ["Plan management", "Add plan", "/ 1 mo", "/ item"]],
      ["/admin/settings", ["System and integration settings", "Site details", "encrypted body"]],
    ];
    for (const [route, labels] of adminRoutes) {
      const text = await assertPreferences(adminPage, route);
      for (const label of labels) if (!text.includes(label)) throw new Error(`${route} English translation is missing: ${label}`);
    }

    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 } });
    await mobile.addCookies([{ name: "vps_session", value: customerCookie, url: baseURL }]);
    await mobile.addInitScript(() => localStorage.setItem("vps-one-ui", JSON.stringify({ skin: "glass", mode: "light", language: "zh-CN" })));
    const mobilePage = await mobile.newPage();
    mobilePage.on("pageerror", (error) => pageErrors.push(error.message));
    await assertPreferences(mobilePage, "/account");
    state = await mobilePage.evaluate(() => window.VPS_UI.getState());
    if (state.skin !== "glass" || state.color !== "light" || state.language !== "zh-CN") throw new Error(`GLASS UI state did not apply: ${JSON.stringify(state)}`);
    panel = await openPreferences(mobilePage);
    const panelBox = await panel.boundingBox();
    if (!panelBox || panelBox.x < 0 || panelBox.x + panelBox.width > 390) throw new Error("Mobile preferences panel is outside the viewport");
    await mobilePage.locator(".ui-panel-close").click();
    await mobilePage.screenshot({ path: screenshots.glass, fullPage: true });

    const mobileAdmin = await browser.newContext({ viewport: { width: 390, height: 844 } });
    await mobileAdmin.addCookies([{ name: "vps_session", value: adminCookie, url: baseURL }]);
    await mobileAdmin.addInitScript(() => localStorage.setItem("vps-one-ui", JSON.stringify({ skin: "dashboard", mode: "light", language: "zh-CN" })));
    const mobileAdminPage = await mobileAdmin.newPage();
    mobileAdminPage.on("pageerror", (error) => pageErrors.push(error.message));
    await assertPreferences(mobileAdminPage, "/admin/plans");

    if (pageErrors.length) throw new Error(`Browser errors: ${pageErrors.join("; ")}`);
    console.log(JSON.stringify({
      defaultSkin: "dashboard/light/zh-CN",
      newSkin: "newskin/dark/en",
      glassSkin: "glass/light/zh-CN",
      checkedRouteVisits: 12,
      mobileOverflow: false,
      pageErrors,
      screenshots,
    }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
