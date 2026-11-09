---
name: fab-library
description: 用 fab-library CLI 查询和整理用户已拥有的 Fab Unreal Engine 资源、蓝图与插件，管理中文标签、下载计划和队列。用于 Fab 账号资源库与本地下载；不用于购买资源或编辑 UE 蓝图。
---

# Fab Library

这是 Windows 优先的个人 CLI，依赖固定版本 `fab-downloader 1.3.0`，不属于 Epic 官方工具。

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

## 示例

```powershell
fab-library --json library list --tag "背包系统" --engine 5.8 --limit 20
fab-library --json assets resolve "Inventory"
fab-library --json download ASSET_ID --engine 5.8 --out "D:\FabAssets\Inventory-5.8" --dry-run
```

成功 JSON：`{"schema_version":1,"ok":true,"data":...}`。失败返回非零退出码及 `error.kind/message/details`。进度只写 stderr。不要把规划完成、fixture 测试通过或本地文件存在说成已完成真实服务器下载验证。
