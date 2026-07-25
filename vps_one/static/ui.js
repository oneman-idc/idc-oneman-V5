(() => {
  "use strict";

  const STORAGE_KEY = "vps-one-ui";
  const SKINS = new Set(["dashboard", "newskin", "glass"]);
  const MODES = new Set(["system", "light", "dark"]);
  const LANGUAGES = new Set(["zh-CN", "en"]);
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  const translations = Object.freeze({
    "登录｜VPS-ONE": "Sign in | VPS-ONE",
    "注册｜VPS-ONE": "Register | VPS-ONE",
    "安装 VPS-ONE": "Install VPS-ONE",
    "个人中心｜VPS-ONE": "Account | VPS-ONE",
    "服务器管理｜VPS-ONEMAN": "Server Management | VPS-ONEMAN",
    "管理后台｜VPS-ONE": "Admin | VPS-ONE",
    "退款审核｜VPS-ONE": "Refund Review | VPS-ONE",
    "套餐管理｜VPS-ONE": "Plan Management | VPS-ONE",
    "产品控制｜VPS-ONE": "Product Control | VPS-ONE",
    "系统配置｜VPS-ONE": "Settings | VPS-ONE",
    "云主机与数字商品自动支付交付": "Automated payment and delivery for cloud servers and digital goods",
    "每只鸡都是好机，": "Every server is built to perform,",
    "绝不吃灰": "and built to be used",
    "高性能容器云": "High-performance container cloud",
    "稳定算力，专注增长": "Reliable compute, focused growth",
    "产品": "Products",
    "控制台": "Console",
    "退出": "Sign out",
    "登录": "Sign in",
    "免费注册": "Register",
    "选择产品": "Browse products",
    "进入客户中心 →": "Open customer center →",
    "产品套餐": "Product plans",
    "云主机自动开通，数字商品自动发送至注册邮箱": "Cloud servers are provisioned automatically; digital goods are emailed to your registered address.",
    "自动发卡": "Digital delivery",
    "云主机": "Cloud server",
    "售罄": "Sold out",
    "可用": "Available",
    "付款后 SMTP 自动交付": "Automatic SMTP delivery after payment",
    "页面仅显示脱敏卡密": "Only a masked code is shown here",
    "完整卡密发送至注册邮箱": "The full code is sent to your registered email",
    "钱包购买": "Pay with wallet",
    "HashPay 支付": "Pay with HashPay",
    "登录购买": "Sign in to buy",
    "暂时售罄": "Out of stock",
    "暂无在售产品": "No products are currently available",
    "欢迎回来": "Welcome back",
    "邮箱": "Email",
    "密码": "Password",
    "还没有账户？": "New here?",
    "立即注册": "Create an account",
    "创建账户": "Create account",
    "注册": "Register",
    "已有账户？": "Already registered?",
    "返回登录": "Back to sign in",
    "初始化系统": "Initialize system",
    "创建首个管理员账户，完成后安装入口将自动锁定。": "Create the first administrator account. The installer will lock automatically when finished.",
    "管理员邮箱": "Administrator email",
    "管理员密码": "Administrator password",
    "完成安装": "Complete installation",
    "我的云主机": "My cloud servers",
    "购买产品": "Buy products",
    "服务器管理": "Server management",
    "SSH 密码已重置，完整云主机信息已发送至您的注册邮箱。": "The SSH password was reset and full server details were sent to your registered email.",
    "SSH 密码已成功重置，但邮件发送失败。请联系管理员检查配置。": "The SSH password was reset, but email delivery failed. Ask an administrator to check the configuration.",
    "钱包扣款成功，云主机正在自动交付。": "Wallet payment completed. Your cloud server is being provisioned.",
    "套餐名称": "Plan",
    "到期时间": "Expires",
    "订单号": "Order number",
    "订单时间": "Ordered at",
    "商品基本配置": "Base configuration",
    "详细": "Details",
    "主机详情": "Server details",
    "服务标识": "Service ID",
    "操作系统": "Operating system",
    "机密信息": "Credentials",
    "公网 IP": "Public IP",
    "SSH 端口": "SSH port",
    "SSH 密码": "SSH password",
    "机器配置": "Machine configuration",
    "套餐": "Plan",
    "基础资源": "Base resources",
    "流量": "Traffic",
    "磁盘": "Disk",
    "带宽": "Bandwidth",
    "控制台信息": "Console access",
    "用户名": "Username",
    "初始密码": "Initial password",
    "访问码": "Access code",
    "访问链接": "Access URL",
    "打开访问链接": "Open access URL",
    "未返回": "Not returned",
    "开机": "Start",
    "关机": "Stop",
    "重启": "Restart",
    "重置 SSH 密码": "Reset SSH password",
    "重置密码": "Reset password",
    "请重置 SSH 密码并查收邮件": "Reset the SSH password and check your email.",
    "暂无已交付主机，支付完成后系统会自动创建并启动云主机。": "No delivered servers yet. A server will be created and started after payment.",
    "处理中": "Processing",
    "待交付订单": "Orders awaiting delivery",
    "继续支付": "Continue payment",
    "查看发卡进度": "View delivery status",
    "发卡任务暂时失败，系统将自动重试": "Digital delivery failed temporarily and will retry automatically.",
    "重新连接": "Reconnect",
    "关闭 VNC": "Close VNC",
    "连接中": "Connecting",
    "已连接": "Connected",
    "连接失败": "Connection failed",
    "连接已断开，请确认虚拟机正在运行且控制台可用": "The connection closed. Confirm that the VM is running and its console is available.",
    "VNC 安全协商失败": "VNC security negotiation failed",
    "VNC 控制台要求额外密码": "The VNC console requires additional credentials",
    "WebVNC 会话创建失败": "Could not create a WebVNC session",
    "WebVNC 初始化失败": "WebVNC initialization failed",
    "请先启动虚拟机": "Start the virtual machine first",
    "个人信息": "Account",
    "个人中心": "Account",
    "钱包余额": "Wallet balance",
    "充值结果以 HashPay 异步回调为准，到账后余额与流水会自动更新。": "Top-up status follows the HashPay callback. The balance and ledger update automatically.",
    "确认码已发送至注册邮箱，请在订单中完成确认。": "A confirmation code was sent to your registered email. Complete confirmation in the order.",
    "确认邮件发送失败，请检查邮箱后重试发送或联系管理员。": "Confirmation email failed. Verify the address, retry, or contact an administrator.",
    "撤销申请已确认，正在等待管理员审核。": "The cancellation request is confirmed and awaiting review.",
    "确认码错误；连续输错 5 次后需要重新发送。": "The confirmation code is invalid. After five failed attempts, request a new code.",
    "确认码已过期，请在订单中重新发送。": "The confirmation code expired. Request a new one from the order.",
    "钱包扣款成功，卡密正在发送至注册邮箱。": "Wallet payment completed. The code is being emailed to your registered address.",
    "支付结果以 HashPay 异步回调为准，发卡状态会自动更新。": "Payment status follows the HashPay callback. Delivery status updates automatically.",
    "卡密邮件已重新进入发送队列。": "The code email was queued again.",
    "充值金额": "Top-up amount",
    "HashPay 充值": "Top up with HashPay",
    "使用钱包购买套餐": "Buy a plan with wallet",
    "钱包流水": "Wallet ledger",
    "充值记录": "Top-up history",
    "时间": "Time",
    "说明": "Description",
    "变动": "Change",
    "余额": "Balance",
    "暂无钱包流水": "No wallet entries",
    "充值单": "Top-up number",
    "金额": "Amount",
    "状态": "Status",
    "暂无充值记录": "No top-up records",
    "订单管理": "Order management",
    "订单列表": "Orders",
    "支付方式": "Payment method",
    "支付时间": "Paid at",
    "未支付": "Unpaid",
    "卡密摘要": "Masked code",
    "待分配": "Awaiting allocation",
    "邮件交付": "Email delivery",
    "尚未发送": "Not sent",
    "邮件交付异常，系统会自动重试；持续失败请联系管理员。": "Email delivery failed and will retry automatically. Contact an administrator if it continues.",
    "重新发送到注册邮箱": "Resend to registered email",
    "完整卡密仅通过注册邮箱交付，页面仅保留脱敏摘要。": "The full code is delivered only by email. This page keeps a masked summary.",
    "撤销申请": "Cancellation request",
    "位确认码": "-digit code",
    "确认撤销": "Confirm cancellation",
    "重新发送确认码": "Send a new code",
    "选择撤销原因": "Select a cancellation reason",
    "购买错误": "Purchased by mistake",
    "配置不符合预期": "Configuration did not meet expectations",
    "不再需要服务": "Service no longer needed",
    "其他原因": "Other",
    "我确认审核通过后云主机将被销毁": "I understand that the server will be destroyed after approval.",
    "撤销": "Cancel order",
    "撤销窗口已关闭": "Cancellation window closed",
    "暂无订单": "No orders",
    "运营总览": "Overview",
    "退款审核": "Refund review",
    "产品控制": "Product control",
    "套餐管理": "Plan management",
    "系统配置": "Settings",
    "客户控制台": "Customer console",
    "返回商城": "Back to storefront",
    "用户": "Users",
    "订单": "Orders",
    "实例": "Instances",
    "卡密": "Codes",
    "退款申请": "Refund requests",
    "任务": "Jobs",
    "最新订单": "Latest orders",
    "类型": "Type",
    "任务队列": "Job queue",
    "尝试": "Attempts",
    "错误": "Error",
    "审核已通过，退款任务已进入队列。": "Review approved. The refund job has been queued.",
    "申请": "Request",
    "用户 / 订单": "User / order",
    "原因": "Reason",
    "操作": "Actions",
    "未填写": "Not provided",
    "缺失": "Missing",
    "审核说明": "Review note",
    "批准": "Approve",
    "拒绝": "Reject",
    "重试处理": "Retry processing",
    "暂无退款申请": "No refund requests",
    "容器管理": "Container management",
    "直接通过 CLICD API 管理容器、资源限制与平台运行状态。": "Manage containers, resource limits, and platform status through the CLICD API.",
    "容器总数": "Containers",
    "运行中": "Running",
    "已停止": "Stopped",
    "地址": "Address",
    "资源与流量限制": "Resource and traffic limits",
    "内存": "Memory",
    "下载 Mbps": "Download Mbps",
    "上传 Mbps": "Upload Mbps",
    "读 IO MB/s": "Read I/O MB/s",
    "写 IO MB/s": "Write I/O MB/s",
    "月流量 GB": "Monthly traffic GB",
    "保存限制": "Save limits",
    "输入": "Enter",
    "永久删除": "Delete permanently",
    "删除容器": "Delete container",
    "暂无容器数据": "No container data",
    "新增套餐": "Add plan",
    "名称": "Name",
    "唯一标识": "Unique slug",
    "套餐类型": "Product type",
    "价格（分）": "Price (cents)",
    "周期（月）": "Term (months)",
    "库存（-1 无限）": "Stock (-1 unlimited)",
    "vCPU": "vCPU",
    "内存 MB": "Memory MB",
    "磁盘 GB": "Disk GB",
    "下行 Mbps": "Download Mbps",
    "上行 Mbps": "Upload Mbps",
    "虚拟化": "Virtualization",
    "CLICD 节点与镜像": "CLICD node and image",
    "请选择 LXC 节点与镜像": "Select an LXC node and image",
    "交付说明": "Delivery instructions",
    "初始卡密库存": "Initial code inventory",
    "每行一条卡密，支付成功后按导入顺序发放": "One code per line; codes are delivered in import order after payment",
    "NAT": "NAT",
    "端口数量": "Port count",
    "公网 IPv4": "Public IPv4",
    "上架": "Publish",
    "保存套餐": "Save plan",
    "无额外交付说明": "No additional delivery instructions",
    "追加卡密": "Add codes",
    "每行一条": "One per line",
    "导入库存": "Import inventory",
    "默认 CLICD 节点": "Default CLICD node",
    "NAT 已关闭": "NAT disabled",
    "下架": "Unpublish",
    "最近发卡订单": "Recent digital deliveries",
    "脱敏卡密": "Masked code",
    "邮件": "Email",
    "待分配": "Awaiting allocation",
    "未发送": "Not sent",
    "重试发卡": "Retry delivery",
    "已完成": "Completed",
    "暂无发卡订单": "No digital delivery orders",
    "卡密导入完成": "Code import completed",
    "发卡任务已重新进入队列。": "The delivery job was queued again.",
    "系统与集成配置": "System and integration settings",
    "配置已保存或测试通过": "Configuration saved or test completed",
    "网站基础信息": "Site details",
    "网站名称": "Site name",
    "网站副标题": "Site tagline",
    "站点公开地址": "Public site URL",
    "全局用于 HashPay 回调与支付返回地址；保存后覆盖环境变量 BASE_URL。": "Used globally for HashPay callbacks and return URLs. Saving overrides BASE_URL.",
    "页脚文案": "Footer text",
    "测试全部节点": "Test all nodes",
    "面板地址（每行一个）": "Panel URLs (one per line)",
    "API Key（按行对应）": "API keys (matching line order)",
    "留空保留现值；修改时填写全部 Key": "Leave blank to keep current values; enter every key when changing",
    "HashPay 支付": "HashPay payment",
    "直接连接外部 HashPay 商户 API。": "Connect directly to the external HashPay merchant API.",
    "测试地址": "Test endpoint",
    "HashPay 地址": "HashPay URL",
    "商户 ID": "Merchant ID",
    "商户 RSA 私钥": "Merchant RSA private key",
    "HashPay RSA 公钥": "HashPay RSA public key",
    "留空保留现值": "Leave blank to keep the current value",
    "回调地址：": "Callback URL:",
    "请先配置站点地址": "Configure the site URL first",
    "HashPay 回调需携带": "HashPay callbacks must include",
    "正文为 RSA-OAEP/AES-GCM 加密包。": "and use an RSA-OAEP/AES-GCM encrypted body.",
    "，正文为 RSA-OAEP/AES-GCM 加密包。": ", with an RSA-OAEP/AES-GCM encrypted body.",
    "SMTP 邮件": "SMTP email",
    "SMTP 主机": "SMTP host",
    "端口": "Port",
    "安全": "Security",
    "无": "None",
    "发件人": "Sender",
    "保存全部配置": "Save all settings",
    "待支付": "Awaiting payment",
    "支付失败": "Payment failed",
    "已支付": "Paid",
    "交付中": "Provisioning",
    "发卡中": "Delivering",
    "发卡异常": "Delivery issue",
    "已退款": "Refunded",
    "待邮箱确认": "Awaiting email confirmation",
    "验证码已锁定": "Confirmation code locked",
    "邮件发送失败": "Email failed",
    "待后台审核": "Awaiting review",
    "审核通过": "Approved",
    "退款处理中": "Refund processing",
    "处理失败": "Processing failed",
    "审核拒绝": "Rejected",
    "已过期": "Expired",
    "已关机": "Powered off",
    "启动中": "Starting",
    "关机中": "Stopping",
    "重启中": "Restarting",
    "创建中": "Creating",
    "部署中": "Provisioning",
    "等待中": "Waiting",
    "状态未知": "Unknown status",
    "邮箱或密码错误": "Incorrect email or password",
    "请输入有效邮箱": "Enter a valid email address",
    "密码至少 10 位，且包含字母和数字": "Password must be at least 10 characters and include letters and numbers",
    "邮箱已注册": "This email is already registered"
  });

  const reverseTranslations = Object.freeze(Object.fromEntries(
    Object.entries(translations).map(([chinese, english]) => [english, chinese]),
  ));

  const patterns = [
    [/^(.+)\s+实惠 \/ 简单 \/ 专业 \/ 性价比高，从此交付如此简单。$/, (_, tagline) => `${translations[tagline] || tagline} · Affordable, simple, professional, and cost-effective. Delivery made easy.`, /^(.+)\s*·\s*Affordable, simple, professional, and cost-effective\. Delivery made easy\.$/, (_, tagline) => `${reverseTranslations[tagline] || tagline} 实惠 / 简单 / 专业 / 性价比高，从此交付如此简单。`],
    [/^(.+)｜高性能容器云$/, (_, siteName) => `${siteName} | High-performance container cloud`, /^(.+) \| High-performance container cloud$/, (_, siteName) => `${siteName}｜高性能容器云`],
    [/^(\d+)月$/, (_, count) => `${count} mo`, /^(\d+) mo$/, (_, count) => `${count}月`],
    [/^份$/, () => "item", /^item$/, () => "份"],
    [/^\/\s*份$/, () => "/item", /^\/\s*item$/, () => "/份"],
    [/^\/\s*(\d+)\s*月$/, (_, count) => `/${count} mo`, /^\/\s*(\d+)\s*mo$/, (_, count) => `/${count}月`],
    [/^(.+?)\s*\/\s*份$/, (_, value) => `${value} / item`, /^(.+?)\s*\/\s*item$/, (_, value) => `${value} / 份`],
    [/^(.+?)\s*\/\s*(\d+)\s*月$/, (_, value, count) => `${value} / ${count} mo`, /^(.+?)\s*\/\s*(\d+)\s*mo$/, (_, value, count) => `${value} / ${count} 月`],
    [/^MB\s+内存$/, () => "MB memory", /^MB\s+memory$/, () => "MB 内存"],
    [/^GB\s+高速硬盘$/, () => "GB high-speed disk", /^GB\s+high-speed disk$/, () => "GB 高速硬盘"],
    [/^GB\s+流量$/, () => "GB traffic", /^GB\s+traffic$/, () => "GB 流量"],
    [/^Mbps\s*·\s*(\d+)\s+GB\s+流量$/, (_, traffic) => `Mbps · ${traffic} GB traffic`, /^Mbps\s*·\s*(\d+)\s+GB\s+traffic$/, (_, traffic) => `Mbps · ${traffic} GB 流量`],
    [/^(\d+)\s+位确认码$/, (_, count) => `${count}-digit code`, /^(\d+)-digit code$/, (_, count) => `${count} 位确认码`],
    [/^已配置\s+(\d+)\s+个节点。$/, (_, count) => `Configured nodes: ${count}.`, /^Configured nodes:\s+(\d+)\.$/, (_, count) => `已配置 ${count} 个节点。`],
    [/^剩余库存\s+(-?\d+)$/, (_, count) => `Stock remaining: ${count}`, /^Stock remaining:\s+(-?\d+)$/, (_, count) => `剩余库存 ${count}`],
    [/^钱包购买\s*·\s*(.+)$/, (_, amount) => `Wallet · ${amount}`, /^Wallet\s*·\s*(.+)$/, (_, amount) => `钱包购买 · ${amount}`],
    [/^24 小时内剩余可提交次数：\s*(\d+)$/, (_, count) => `Requests remaining in 24 hours: ${count}`, /^Requests remaining in 24 hours:\s*(\d+)$/, (_, count) => `24 小时内剩余可提交次数：${count}`],
    [/^截止\s+(.+)$/, (_, date) => `Deadline ${date}`, /^Deadline\s+(.+)$/, (_, date) => `截止 ${date}`],
    [/^卡密导入完成：新增\s+(\d+)\s+条，跳过\s+(\d+)\s+条。$/, (_, added, skipped) => `Code import completed: ${added} added, ${skipped} skipped.`, /^Code import completed:\s+(\d+)\s+added,\s+(\d+)\s+skipped\.$/, (_, added, skipped) => `卡密导入完成：新增 ${added} 条，跳过 ${skipped} 条。`],
    [/^可用\s+(\d+)\s*·\s*已分配\s+(\d+)\s*·\s*已交付\s+(\d+)$/, (_, available, assigned, delivered) => `Available ${available} · Assigned ${assigned} · Delivered ${delivered}`, /^Available\s+(\d+)\s*·\s*Assigned\s+(\d+)\s*·\s*Delivered\s+(\d+)$/, (_, available, assigned, delivered) => `可用 ${available} · 已分配 ${assigned} · 已交付 ${delivered}`],
    [/^NAT\s*·\s*(\d+)\s+个端口$/, (_, count) => `NAT · ${count} ports`, /^NAT\s*·\s*(\d+)\s+ports$/, (_, count) => `NAT · ${count} 个端口`],
    [/^(.+)\s*·\s*自动发卡$/, (_, name) => `${name} · Digital delivery`, /^(.+)\s*·\s*Digital delivery$/, (_, name) => `${name} · 自动发卡`],
    [/^(.+)\s*·\s*云主机$/, (_, name) => `${name} · Cloud server`, /^(.+)\s*·\s*Cloud server$/, (_, name) => `${name} · 云主机`],
    [/^(\d+)\s*核\s*·\s*(.+)$/, (_, cores, rest) => `${cores} cores · ${rest}`, /^(\d+)\s+cores\s*·\s*(.+)$/, (_, cores, rest) => `${cores} 核 · ${rest}`],
    [/^撤销申请\s+(.+)$/, (_, number) => `Cancellation request ${number}`, /^Cancellation request\s+(.+)$/, (_, number) => `撤销申请 ${number}`],
    [/^请选择\s+([A-Z]+)\s+节点与镜像$/, (_, kind) => `Select a ${kind} node and image`, /^Select a\s+([A-Z]+)\s+node and image$/, (_, kind) => `请选择 ${kind} 节点与镜像`],
    [/^暂无可用的\s+([A-Z]+)\s+镜像$/, (_, kind) => `No ${kind} images available`, /^No\s+([A-Z]+)\s+images available$/, (_, kind) => `暂无可用的 ${kind} 镜像`],
  ];

  const uiCopy = {
    "zh-CN": {
      trigger: "界面设置",
      title: "界面与语言",
      close: "关闭设置",
      skin: "界面模板",
      mode: "明暗模式",
      language: "语言",
      system: "跟随系统",
      light: "白天",
      dark: "黑夜",
    },
    en: {
      trigger: "Interface settings",
      title: "Interface & language",
      close: "Close settings",
      skin: "Interface skin",
      mode: "Appearance",
      language: "Language",
      system: "System",
      light: "Light",
      dark: "Dark",
    },
  };

  function readState() {
    let saved = {};
    try {
      saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch (_) {
      saved = {};
    }
    return {
      skin: SKINS.has(saved.skin) ? saved.skin : "dashboard",
      mode: MODES.has(saved.mode) ? saved.mode : "system",
      language: LANGUAGES.has(saved.language) ? saved.language : "zh-CN",
    };
  }

  const state = readState();

  function resolvedColor() {
    if (state.mode !== "system") return state.mode === "dark" ? "dark" : "light";
    return media && media.matches ? "dark" : "light";
  }

  function applyRootState() {
    const root = document.documentElement;
    root.dataset.skin = state.skin;
    root.dataset.mode = state.mode;
    root.dataset.color = resolvedColor();
    root.lang = state.language;
  }

  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (_) {
      // The active preference still applies when storage is unavailable.
    }
  }

  function patternTranslation(value, language) {
    for (const [zhPattern, toEnglish, enPattern, toChinese] of patterns) {
      const match = value.match(language === "en" ? zhPattern : enPattern);
      if (match) return (language === "en" ? toEnglish : toChinese)(...match);
    }
    return value;
  }

  function translatedValue(raw, language = state.language) {
    if (!raw || !raw.trim()) return raw;
    const leading = raw.match(/^\s*/)[0];
    const trailing = raw.match(/\s*$/)[0];
    const value = raw.trim();
    const exact = language === "en" ? translations[value] : reverseTranslations[value];
    const translated = exact || patternTranslation(value, language);
    return `${leading}${translated}${trailing}`;
  }

  function translateElement(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || ["SCRIPT", "STYLE", "CODE", "PRE"].includes(parent.tagName)) return NodeFilter.FILTER_REJECT;
        return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const next = translatedValue(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
    }
    const elements = root.querySelectorAll ? root.querySelectorAll("[placeholder],[title],[aria-label],meta[content]") : [];
    for (const element of elements) {
      for (const attribute of ["placeholder", "title", "aria-label", "content"]) {
        if (!element.hasAttribute(attribute)) continue;
        const current = element.getAttribute(attribute);
        const next = translatedValue(current);
        if (next !== current) element.setAttribute(attribute, next);
      }
    }
  }

  function panelMarkup(copy) {
    return `
      <button class="ui-settings-trigger" type="button" aria-expanded="false" aria-controls="ui-preferences-panel" aria-label="${copy.trigger}" title="${copy.trigger}">◐</button>
      <section id="ui-preferences-panel" class="ui-preferences-panel" aria-label="${copy.title}" hidden>
        <div class="ui-panel-head"><strong data-ui-copy="title">${copy.title}</strong><button class="ui-panel-close" type="button" aria-label="${copy.close}" title="${copy.close}">×</button></div>
        <label class="ui-setting"><span data-ui-copy="skin">${copy.skin}</span><span class="ui-segment" role="group" aria-label="${copy.skin}">
          <button type="button" data-ui-setting="skin" data-ui-value="dashboard">DEFAULT</button>
          <button type="button" data-ui-setting="skin" data-ui-value="newskin">NEW SKIN</button>
          <button type="button" data-ui-setting="skin" data-ui-value="glass">GLASS UI</button>
        </span></label>
        <label class="ui-setting"><span data-ui-copy="mode">${copy.mode}</span><span class="ui-segment" role="group" aria-label="${copy.mode}">
          <button type="button" data-ui-setting="mode" data-ui-value="system" data-ui-copy="system">${copy.system}</button>
          <button type="button" data-ui-setting="mode" data-ui-value="light" data-ui-copy="light">${copy.light}</button>
          <button type="button" data-ui-setting="mode" data-ui-value="dark" data-ui-copy="dark">${copy.dark}</button>
        </span></label>
        <label class="ui-setting"><span data-ui-copy="language">${copy.language}</span><span class="ui-segment" role="group" aria-label="${copy.language}">
          <button type="button" data-ui-setting="language" data-ui-value="zh-CN">中文</button>
          <button type="button" data-ui-setting="language" data-ui-value="en">EN</button>
        </span></label>
      </section>`;
  }

  let preferences = null;

  function updatePreferenceControls() {
    if (!preferences) return;
    const copy = uiCopy[state.language];
    const trigger = preferences.querySelector(".ui-settings-trigger");
    const panel = preferences.querySelector(".ui-preferences-panel");
    trigger.setAttribute("aria-label", copy.trigger);
    trigger.title = copy.trigger;
    panel.setAttribute("aria-label", copy.title);
    const close = preferences.querySelector(".ui-panel-close");
    close.setAttribute("aria-label", copy.close);
    close.title = copy.close;
    for (const element of preferences.querySelectorAll("[data-ui-copy]")) {
      element.textContent = copy[element.dataset.uiCopy];
    }
    for (const group of preferences.querySelectorAll(".ui-setting")) {
      const label = group.querySelector(":scope > span:first-child").textContent;
      group.querySelector("[role='group']").setAttribute("aria-label", label);
    }
    for (const button of preferences.querySelectorAll("[data-ui-setting]")) {
      button.setAttribute("aria-pressed", String(state[button.dataset.uiSetting] === button.dataset.uiValue));
    }
  }

  function setPreference(setting, value) {
    if (setting === "skin" && !SKINS.has(value)) return;
    if (setting === "mode" && !MODES.has(value)) return;
    if (setting === "language" && !LANGUAGES.has(value)) return;
    state[setting] = value;
    saveState();
    applyRootState();
    translateElement(document.documentElement);
    updatePreferenceControls();
  }

  function mountPreferences() {
    preferences = document.createElement("div");
    preferences.className = "ui-preferences";
    preferences.innerHTML = panelMarkup(uiCopy[state.language]);
    const nav = document.querySelector(".nav .links");
    if (nav) {
      nav.append(preferences);
    } else {
      preferences.classList.add("ui-preferences-floating");
      document.body.append(preferences);
    }
    const trigger = preferences.querySelector(".ui-settings-trigger");
    const panel = preferences.querySelector(".ui-preferences-panel");
    const closePanel = () => {
      panel.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    };
    trigger.addEventListener("click", () => {
      const opening = panel.hidden;
      panel.hidden = !opening;
      trigger.setAttribute("aria-expanded", String(opening));
      if (opening) panel.querySelector("button")?.focus();
    });
    preferences.querySelector(".ui-panel-close").addEventListener("click", closePanel);
    preferences.addEventListener("click", (event) => {
      const button = event.target.closest("[data-ui-setting]");
      if (button) setPreference(button.dataset.uiSetting, button.dataset.uiValue);
    });
    document.addEventListener("click", (event) => {
      if (!panel.hidden && !preferences.contains(event.target)) closePanel();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !panel.hidden) {
        closePanel();
        trigger.focus();
      }
    });
    updatePreferenceControls();
  }

  applyRootState();

  if (media) {
    media.addEventListener("change", () => {
      if (state.mode === "system") applyRootState();
    });
  }

  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    Object.assign(state, readState());
    applyRootState();
    translateElement(document.documentElement);
    updatePreferenceControls();
  });

  window.addEventListener("DOMContentLoaded", () => {
    translateElement(document.documentElement);
    mountPreferences();
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === "characterData") {
          const next = translatedValue(mutation.target.nodeValue);
          if (next !== mutation.target.nodeValue) mutation.target.nodeValue = next;
        }
        for (const node of mutation.addedNodes) {
          if (node.nodeType === Node.ELEMENT_NODE) translateElement(node);
          if (node.nodeType === Node.TEXT_NODE) {
            const next = translatedValue(node.nodeValue);
            if (next !== node.nodeValue) node.nodeValue = next;
          }
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  });

  window.VPS_UI = {
    getState: () => ({ ...state, color: resolvedColor() }),
    setPreference,
  };
})();
