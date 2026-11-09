---
name: fab-library
description: 用 fab-library CLI 管理 Fab UE 资源库、标签和下载，查询免费资源并领取已授权的零元条目，整理付费资源清单及购买链接。不用于自动付款或编辑 UE 蓝图。
---

# Fab Library

这是 Windows 优先的个人 CLI，下载依赖 `fab-downloader 1.3.0`，可选商店后端为 `FabCLI 0.1.0`；均不属于 Epic 官方工具。

## 开始

1. 在 Windows 用 `Get-Command fab-library` 确认命令存在，其他系统用 `command -v fab-library`。从任何目录都应可调用。缺失时使用交付包中的 `scripts/install-windows.ps1`；不要临时发明另一个同名脚本。
2. 运行 `fab-library --json doctor`。默认不联网。检查 `mode`、`auth.token_available`、目录和后端版本。`token_available` 仅表示有本地会话，不代表在线验证成功。
3. 需要真实账号但未登录时，让用户在 Windows 终端执行 `fab-library auth login`，在 Epic 页面完成登录并将授权码输入本地隐藏提示。不要让用户把密码、授权码、令牌发到聊天；不要读取浏览器 cookie 或其他应用的会话。
4. 初次使用或用户要求刷新时执行 `fab-library --json library sync`。平时使用本地查询，避免每次搜索都同步全库。

## 查询与整理

- `library list` 支持 `--query`、`--tag`、`--engine`、`--limit`、`--offset`，默认 25 条，跟随真实的 `next_offset` 分页。
- 用 `assets resolve "名称"` 解析成稳定 ID；多个结果时不能任意选第一项。后续用 `assets show ID`。
- 中文分类通过本地 `tags add ID "背包系统"` 保存。目录中的英文名称未匹配中文时，不要声称没有该资源；可扩大检索、解释候选并添加用户需要的标签。
- `assets raw ID` 是只读补充入口，返回本地快照中经过字段白名单处理的提供方元数据，不调用任意 HTTP 地址。
- 快照只包含标题、分发类型、UE 版本与 artifact ID；不包含完整商品详情、图片、购买价格或自动的语义检索。

## 下载

先解析资源 ID、核对用户要的 UE 版本和目录，再用 `download ID --engine 5.8 --out "D:\FabAssets\独立子目录" --dry-run` 查看计划。默认平台为 Windows；不能据此推断该插件支持 Mac。

用户已授权下载这些资源和目标目录时，去掉 `--dry-run` 执行，无需重复询问。目标目录必须是这个单独资源的新目录。已有内容不会被覆盖；完整匹配的下载收据及文件大小检查通过时会跳过下载。不要自行删除不匹配的目录。

批量任务先逐个 `queue add ID --engine ... --out ...`，用 `queue list` 核对，再 `queue run --limit N`。队列按顺序执行，遇到第一个失败就停止。查看结构化错误；仅对已授权任务进行有界重试，使用 `queue retry JOB_ID` 后再次运行。403/429 时不要并发反复重试或绕过登录保护。

这版只下载到独立目录，不自动安装或覆盖项目/引擎中的资源。`downloads` 查看本工具记录的下载。它不会自动扫描全部硬盘、发现手动安装的插件或记录项目引用。

`fixture` 模式仅用于离线演示，禁止将其结果描述为用户真实资源；可搜索、打标签、做下载预览，执行下载会拒绝。

## 商店与免费领取

商店功能先运行 `fab-library --json store doctor`。需要安装后端时使用交付包的 `scripts/install-store-windows.ps1`，安装器固定版本并核验 SHA-256。让用户在本机运行 `fab-library store login` 完成 Epic/Fab 登录；下载的 `auth login` 不能代替商店网页会话。

用 `store account` 查看领取所属账号。若要领取后下载，先确认它与 `auth status` 中下载账号一致。商店的 Listing UUID 与下载目录中的 Asset ID 不同；领取后通过 `library sync` 获取可下载条目。

`store free --limit 20` 查询免费资源，`store free --limited-time` 查询限时免费。搜索结果只是提示，不是最新授权报价，也不代表用户授权领取搜索到的所有商品。

用户要领取“本期限免”时，优先 `freebies plan --out PLAN.json` 生成有限清单，展示其中可领取/已拥有/被阻止条目和账号；用户已授权这份具体清单后执行 `freebies apply PLAN.json`。每次只处理文件中的条目，计划 30 分钟过期，执行时重新检查报价。搜索源是 UE 频道的 100% 折扣候选，不应声称它必然等于 Epic 官网精选赠送清单；需要精确匹配官网活动时，用重复的 `--listing URL` 传入已核对的商品链接。

一次性的领取授权不代表未来持续领取。此版本不创建计划任务、开机自启、后台轮询或定时领取；若用户明确要求这些功能，先展示具体范围、频率、账号和异常处理方式，取得该持续任务的授权后再另行配置。

对用户指定或明确授权的清单，逐项 `store claim LISTING_UUID --dry-run` 查看当前账号、价格、所有权和 `selected_offer_id`。正常执行需要 `--execute --expected-account ACCOUNT_ID --expected-offer OFFER_ID`，参数来自刚核对的预览；若既有授权覆盖该具体领取，无需再重复询问。范围不明确则先展示候选。

领取命令只接受所有报价明确为零的条目。出现混合收费授权、未知价格、免费 Bundle 或过期折扣时，使用返回的官网链接。不要用底层 FabCLI 绕过本工具的拒绝结果。当前后端不提供可靠授权档位标签；用户指定个人/专业等档位时，转官方商品页选择，不要猜 offer ID。

遇到新 EULA、验证码或登录验证时让用户在官方页面完成。`claim_outcome_unknown` 表示可能已经提交，先检查官网/所有权，不要盲目重试。只有 `verified:true` 才能报告本次领取已确认成功。

付费条目用 `store quote` 获取报价和 offer，`shopping add LISTING_UUID --offer OFFER_ID` 保存本地清单，`shopping links` 返回官网购买链接。清单不改在线购物车，不提交订单，不支付；报价是带时间的快照，不能当作最终结算金额。用户另行授权官方界面购买时，按该独立任务处理。

## 示例

```powershell
fab-library --json library list --tag "背包系统" --engine 5.8 --limit 20
fab-library --json assets resolve "Inventory"
fab-library --json download ASSET_ID --engine 5.8 --out "D:\FabAssets\Inventory-5.8" --dry-run
```

成功 JSON：`{"schema_version":1,"ok":true,"data":...}`。失败返回非零退出码及 `error.kind/message/details`。进度只写 stderr。不要把规划完成、fixture 测试通过或本地文件存在说成已完成真实服务器下载验证。
