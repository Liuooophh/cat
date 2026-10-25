# AI 与天气网络

v5 保留原界面与服务商选择。DeepSeek 使用官方 Chat Completions 地址 `https://api.deepseek.com`（也支持 `/v1`），默认 `deepseek-flash`；旧模型名迁移到当前默认。普通桌面聊天显式关闭默认思考模式，连接测试使用正式请求路径并限制输出 16 tokens，不自动重试收费请求。

统一传输层 `pet_network.py` 使用正常 TLS/主机名校验和系统信任证书。环境/Windows HTTP 代理与 `ALL_PROXY` 得到检查；Windows PAC/WPAD 通过 WinHTTP 按目标网址解析，只有配置返回 DIRECT 才直连。代理解析失败时给出具体原因，不自动绕过。SOCKS-only 配置需提供 HTTP 或 mixed 端口；不偷偷改变用户系统设置。诊断不输出代理凭据。

错误区分认证、余额、模型、HTTP 路径、DNS、代理、证书和超时。密钥保存在 Windows 凭据管理器，等价地址使用一致槽位；迁移清理旧槽位，保存失败恢复凭据与设置，设置 JSON 以临时文件原子替换。

天气使用 Open-Meteo，选择并保存具体地点坐标。空搜索结果显示未找到；立即刷新成功后保存会直接采用预览，避免再次请求造成闪回“不可用”。已有成功数据在暂时错误时标记“上次数据”；同名地点更换坐标后，旧请求不会覆盖新地点。详细错误在设置中可见，后台失败会退避重试。

本轮真实验证：系统 PAC 配置存在，WinHTTP 解析成功；公开城市吉隆坡天气请求成功。用户授权的官方 DeepSeek 认证与一次最小生成均 HTTP 200，生成共 9 tokens。该授权测试使用与应用相同的网络层，但没有用真实密钥操作 GUI 按钮；GUI 设置和请求逻辑另有模拟回归。发布包不含测试密钥。

打包验证还发现构建 Python 的 SSL 模块需要一份证书策略 PEM。构建脚本在该资源存在时自动收集到冻结根目录，保留原证书策略；未关闭验证或替换系统证书。完整解压目录不可只复制 EXE；单文件版本需要可写的正常临时目录。

## 本机诊断

先退出旧版本。普通权限 PowerShell 在 EXE 所在目录执行（单文件版替换文件名）：

```powershell
$p = Start-Process '.\PawpalCat-v6.exe' -ArgumentList '--system-diagnostics=system-report.json' -Wait -PassThru
Get-Content '.\system-report.json' -Raw
```

这个模式只采集三次实际 CPU、查询公开测试城市天气、无密钥访问 DeepSeek 模型接口；不打开 GUI、不注册自启动、不读取保存的密钥、不生成收费聊天。DeepSeek **401 是该无密钥连通性检查的预期响应**，不能当作认证成功或失败的证据。检查报告内容，不仅看进程退出码。

真正验证你的账户请打开 AI 设置，选择 DeepSeek、填入自己的密钥并点击测试；可能产生少量费用。默认地址和模型可编辑，不保证账户有余额或权限。天气到“通用设置 → 天气”搜索并选择城市，可先立即刷新后保存。

官方依据：[DeepSeek 接口](https://api-docs.deepseek.com/)、[模型迁移说明](https://api-docs.deepseek.com/news/news260424/)、[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[WinHTTP 自动代理](https://learn.microsoft.com/en-us/windows/win32/winhttp/winhttp-autoproxy-support)。
