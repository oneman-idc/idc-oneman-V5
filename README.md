# IDC-ONEMAN

This is a ONEMAN IDC project bootstrapped with python3.

用本源码当ONEMAN前，请准备好你的CLICD源切鸡，以及HashPay收银台，我只是无聊的组装了一个相对容易的舞台

本人不负责该源码引起的任何风险问题，谢谢理解！

# IDC-ONEMAN 部署说明 

演示DEMO:https://p02--vps--mgsq65kksm7q.code.run/

## 1. 服务器要求

- Debian 11+/Ubuntu 20.04+，或 CentOS Stream/RHEL 8+，Docker环境
- 1 核 CPU、1 GB 内存、10 GB 可用磁盘起步
- 已开放 TCP 9080；生产环境建议由 Nginx/Caddy 反代并启用 HTTPS
- HashPay 商户；销售云主机时还需 CLICD API Key

## 2. 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/oneman-idc/idc-oneman-V5/main/install.sh | sudo sh
```

中国大陆网络不稳定时：

```bash
curl -fsSL https://ghfast.top/https://raw.githubusercontent.com/oneman-idc/idc-oneman-V5/main/install.sh | sudo GITHUB_PROXY=https://ghfast.top USE_CN_MIRROR=1 sh
```

安装脚本自动识别 Debian/RHEL 系、安装 Docker、探测 GitHub、设置 PyPI 和 Docker Hub 国内镜像，并在 Git Clone 失败时回退到 tar.gz 源码包。离线或受限网络可将源码压缩包上传服务器，解压后在源码根目录执行 `sudo USE_CN_MIRROR=1 sh install.sh`。

可选变量：`INSTALL_DIR`、`VPS_ONE_PORT`、`BASE_URL`、`GITHUB_PROXY`、`USE_CN_MIRROR`、`PIP_INDEX_URL`、`DOCKER_REGISTRY`。

## 3. 初始化与后台配置

1. 打开 `http://服务器IP:9080/install`，创建首位管理员。
2. 进入“系统配置”：
   - 站点公开地址必须填写外部可访问的 HTTPS 地址(自行配置反代)。
   - CLICD 面板地址与 API Key。
   - HashPay 地址、商户 ID、私钥和平台公钥。
   - SMTP Host、Port、加密方式、账号、授权码与发件地址。
3. 分别点击 CLICD、HashPay、SMTP 测试按钮。
4. 在“套餐管理”创建产品；云主机需配置 CLICD 节点、镜像和资源，自动发卡套餐则导入卡密库存。
5. HashPay 回调地址为 `https://你的域名/hashpay/callback`。

### 多 CLICD 节点

- “面板地址”和“API Key”均可配置多行，两侧非空行数量必须一致，并按行一一对应。
- 单行配置与旧版本完全兼容。修改 API Key 时需要提交全部 Key；留空会保留数据库中的现值。
- 套餐保存时会同时绑定所选镜像的 CLICD 节点，实例创建后也会固化节点地址；后续同步、开关机、重装、密码、快照和端口操作都会路由到该节点。
- 已有单节点实例升级后会按套餐或远端容器自动补全节点。若多个节点存在相同旧容器 ID 且套餐也未绑定节点，系统会拒绝猜测，需先修正套餐节点。
- 有存量实例时不要直接移除或更换对应面板地址；可更新同一行的 API Key。节点地址不存在时，系统会停止操作并明确报错，避免误操作其他面板。

### 钱包与订单撤销

- 注册时会生成唯一的 6 位小写字母登录名，个人中心钱包与注册邮箱和用户唯一关联。
- 钱包充值复用 HashPay 下单及 `/hashpay/callback` 异步回调；每笔充值、购买和退款都保留不可变流水，回调和退款重试不会重复入账。
- 仅已交付订单可在支付后 24 小时内提交撤销，每个用户滚动 24 小时内最多提交 5 次。
- 撤销需使用注册邮箱收取 6 位确认码；确认码 15 分钟有效，连续错误 5 次后锁定，每笔申请最多发送 5 封确认邮件。生产环境必须先验证 SMTP 可正常投递。
- 管理员在 `/admin/refunds` 审核。批准后后台任务先销毁关联 CLICD 实例，再将费用退入用户钱包；失败会保留原因并可安全重试。该流程不会向原 HashPay 支付渠道发起原路退款。

### 续期与自动续费

- 云主机到期前 7 天会通过已配置的 SMTP 向注册邮箱发送一次续费提醒；Dashboard 和 `/account` 同步显示当前到期时间、续期费用与续期记录。
- 用户可选择钱包或 HashPay 续期。支付完成后后台任务先调用 CLICD `/containers/{id}/expiry` 更新远端到期日，再更新本地实例并发送续期成功邮件；失败任务最多自动重试 5 次，钱包流水与 HashPay 回调均保持幂等。
- 云主机订单默认开启自动续费，在到期前 24 小时至到期后 24 小时的安全窗口内尝试从钱包扣款；余额不足时不会扣款，并只针对当前到期日发送一次充值提醒。用户可在 `/account` 随时开关。
- 自动发卡商品不参与到期提醒、手动续期或自动续费；已交付订单显示“再次购买此套餐”，继续复用普通钱包/HashPay 购买流程。

### 自动发卡套餐

- `/admin/plans` 可创建“自动发卡”套餐。卡密按每行一条导入，去重后使用 `MASTER_KEY` 加密保存；套餐库存由可用卡密数量自动计算，不调用 CLICD。
- 用户可直接复用钱包或 HashPay 购买。支付成功后系统固定分配一条卡密并通过后台任务发送至注册邮箱；SMTP 临时失败会按队列策略重试，后台也可手动重试。
- 商城、Dashboard 和个人中心只展示脱敏摘要（末 4 位），卡密全文仅写入 SMTP 邮件。用户可在已交付订单中重新发送邮件，频率限制为每小时 3 次。
- 已交付的数字卡密不进入云主机 24 小时自助撤销流程，避免卡密泄露后重复销售。售后需由运营人员核验使用状态后线下处理。
- 备份或迁移时必须同时保存 `.env` 中的 `MASTER_KEY` 和 `SECRET_KEY`：前者用于解密库存，后者用于卡密去重指纹；任一密钥丢失都会影响现有库存维护。

### 界面模板、语言与明暗模式

- 全部公开页、客户中心和管理后台右上角提供统一的界面设置入口，并在浏览器本地保存选择。
- `DEFAULT` 是紧凑的运营 Dashboard 风格；`NEW SKIN` 使用 Neumorphism 双向柔和阴影；`GLASS UI` 使用半透明层与背景模糊。
- 三套模板均支持跟随系统、白天、黑夜三种明暗模式，并可即时切换中文或 English；套餐名称、订单号、节点地址等业务数据保持原文。
- 共享实现位于 `vps_one/static/themes.css` 和 `vps_one/static/ui.js`，新增 Jinja 页面时在 `app.css` 后引入 `vps_one/templates/_ui_head.html` 即可继承全部能力。

## 4. 运维命令

在安装目录执行：

```bash
docker compose ps
docker compose logs -f --tail=200
docker compose restart
docker compose pull && docker compose build --pull && docker compose up -d
docker compose exec vps-one python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9080/healthz').read())"
```

备份数据库卷：

```bash
docker compose stop
docker run --rm -v vps-oneman-nb-p5_vps_one_data:/data -v "$PWD":/backup alpine tar czf /backup/vps-one-data.tar.gz -C /data .
docker compose start
```

恢复前必须停止服务，解压备份到同一数据卷。请同时保存 `.env`；丢失 `MASTER_KEY` 后无法解密后台保存的 API/SMTP 密钥。

## 5. 流程

1. 后台 CLICD 切小鸡。
2. 后台 SMTP 邮件发送小鸡关键信息。
3. 使用 HashPay 订单完成支付。
4. IDC-ONEMAN 添加销售套餐 管理用户。


感谢开源代码 CLICD / HashPay / EdgeKey / NodeSeek,大佬们有实力的请随意进行深度二开，JUST DO IT!人人皆是OneMan！
