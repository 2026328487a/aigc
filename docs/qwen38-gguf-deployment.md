# DGX Qwen3.8-27B Uncensored Q6_K 部署

服务器：SSH 别名 `dgx`，DGX Spark / NVIDIA GB10，ARM64，约 121.7 GiB 统一内存。

## 替换对象

已按用户指令删除 `/home/jnu/models/Qwen3.8-27B-NVFP4`，释放约 19.2 GiB。
删除前核对了实际路径、模型配置及活动进程；删除记录保存在
`/home/jnu/models/manifests/qwen38-nvfp4-deletion-20261111.json`。

## llama.cpp

从官方 `v0.9.0` 标签更新，固定提交
`cd959bae99ff4bed22bef2604f45241c09bbb8c8`。
编译结果显示 `0.9.0-dev (build 12110, commit cd959bae9)`；这是该正式版标签源码自身的版本字符串。
CUDA 构建面向 GB10 的架构 `121`，`llama-server` 和 `llama-cli` 已编译通过，MTP 选项存在。

源码及二进制目录：`/home/jnu/src/llama.cpp`、`/home/jnu/src/llama.cpp/build/bin`。

## 模型来源

- 仓库：<https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF>
- 固定 revision：`45d0fc0ad6cfcf9eeee6008ddaea925536409b18`
- 文件：`Qwen3.8-27B-Uncensored-Q6_K.gguf`
- 字节数：`22430999968`（约 20.9 GiB）
- SHA-256：`a50aa1478295b58ee3d93eabe02c17f6d5fcf6cb787fd8a0ab07ac629a46cae6`
- 目标目录：`/home/jnu/models/Qwen3.8-27B-Uncensored-GGUF`

模型已安装到目标目录，本机下载后和服务器传输后均已核验整文件 SHA-256。
中文对话及 API 已于 2026-11-11 05:11–05:12（北京时间）通过验收。

## 服务配置与验收结果

实际使用 32K 上下文、单并发、GPU 完整卸载和内置 MTP 草稿长度 1。
服务器监听 `127.0.0.1:18080`，模型别名为 `qwen38-27b-uncensored-q6-k`。
Windows 可通过 SSH 转发访问浏览器聊天与兼容 API；不开放服务器的公网监听。

### 按需启动与 15 分钟闲置退出

2026-11-11 已进一步将轻量 API 入口纳入自动回收。使用服务器自带的 systemd
socket activation：空闲时仅由已有的 systemd 用户管理器保留
`127.0.0.1:18080` 监听套接字，没有 Qwen 入口、模型或专用转发进程，GPU 占用为零。
下次访问原地址时自动启动入口，第一条请求会排队等待入口就绪。

入口仍使用 llama.cpp router 模式，内部监听 `127.0.0.1:18081`。
启动入口不加载权重（`load-on-startup = false`）；聊天或推理请求自动加载 Q6_K。
首次推理和回收后的首次推理需要等待模型加载。

两层空闲策略均默认 900 秒：

- 子模型连续没有推理任务 900 秒，原生 sleep 先释放权重和 KV 缓存。
- 所有 API 连接关闭、持续 900 秒没有新连接后，`systemd-socket-proxyd` 自动退出。
  后端的 `StopWhenUnneeded=yes` 随之停止 router；`KillMode=control-group`
  回收其全部模型子进程，包括原先约 170 MiB 的入口 CUDA 运行时。

活动连接不会被空闲计时器中断，长请求和流式回复可以继续完成。
健康检查、模型列表及网页轮询不会阻止子模型原生休眠，但会算作入口的使用；
需要入口也退出时，应关闭持续轮询的聊天页或监控。HTTP keep-alive 等待关闭时间
可能使入口实际退出略晚于最后一条请求后 15 分钟。

配置文件位于远端 `~/.config/systemd/user/`：
`qwen38-api.socket`、`qwen38-api.service`、`qwen38-backend.service`。
子模型配置位于 `/home/jnu/work/qwen38-gguf-20261111/qwen-idle.ini`。
配置由 `worker.py start` 生成；启动只监听套接字，不提前启动进程。
`worker.py stop` 停止监听以及全部相关服务，停止后不会再被请求唤醒。
`--idle-seconds` 用于临时验证，正式设置默认 900。

检查是否已经退出应使用以下命令，避免 HTTP 检查本身唤醒入口：

```sh
systemctl --user show qwen38-api.socket qwen38-api.service qwen38-backend.service -p Id -p ActiveState -p MainPID
```

完全闲置时 socket 为 `active`，两个 service 均为 `inactive`、`MainPID=0`。
旧的原生休眠验证记录仍保留在 `test-results/qwen38-idle-unload-*.json`；
完整进程回收与自动唤醒验证由 `scripts/verify_qwen38_socket.py` 执行，
使用临时 5 秒阈值，结束后自动恢复 900 秒。

2026-11-11 12:04（北京时间）完成验证：纯 API 访问后约 5.75 秒入口退出；
实际推理后约 18.16 秒全部退出（含进程和 CUDA 清理时间）；
再次从零进程唤醒后，约 53.92 秒的中文流式请求正常结束，随后全部进程再次回收。
正式配置已恢复 900 秒，Windows 页面 HTTP 200、健康与模型列表均正常；
最终 socket 保持监听，两个 service 均停止，GPU 列表只有原有训练进程。
完整记录保存在 `test-results/qwen38-socket-activation-verification.json`、
`test-results/qwen38-socket-activation-production.json` 和
`test-results/qwen38-socket-windows.json`。

远端工作目录：`/home/jnu/work/qwen38-gguf-20261111`。
构建及下载任务在 SSH 断开后继续运行，状态和日志分别保存在此目录。

验收覆盖 `/health`、`/v1/models`、中文解释、多轮中文记忆、简单算术及流式中文回复。
使用 `chat_template_kwargs.enable_thinking=false` 验证直接回复。
启动参数使用新版推荐的 `--reasoning off` 默认关闭思考，另外验收没有扩展参数的普通客户端请求。

全部验收项通过：

- `/health` 返回 `ok`，`/v1/models` 返回正确模型别名及 `Q6_K` 元数据。
- 中文解释 CPU/GPU 统一内存，返回正常的两句中文。
- 多轮对话正确回忆“你叫小林，最喜欢的饮品是绿茶”。
- `123+456` 只返回 `579`。
- 未传 `chat_template_kwargs` 的普通客户端也直接返回中文，不产生思考内容。
- 流式中文回复正常结束，收到 `[DONE]`，首个内容约 0.531 秒。

此次短请求实测生成约 13.4–16.6 token/s；这不是长上下文或多并发性能测试。
MTP 实际产生并接受了草稿 token。服务启动并完成推理后，GPU 进程报告占用
`23385 MiB`（约 22.8 GiB），系统可用统一内存约 88 GiB。
内置聊天页面已验证 HTTP 200，可使用下面的 Windows 访问入口。

本地验收记录：`test-results/qwen38-api-verification.json`、
`test-results/qwen38-model-verification.json`、`test-results/qwen38-browser-verification.json`。
完整运行状态及清理记录也保存在远端工作目录的
`deployment-verification.json` 和 `cleanup-state.json`。

本地执行脚本：

- `scripts/deploy_qwen38_gguf.py`：远端构建、下载、socket 激活入口启动与停止，默认闲置 900 秒回收。
- `scripts/verify_qwen38_socket.py`：验证入口与模型完整退出、再次按需启动及长流式请求。
- `scripts/download_qwen38_ranges.py`：可续传的分段下载及整文件哈希校验。
- `scripts/verify_qwen38_api.py`：实际 HTTP API 验收。
- `scripts/start_qwen38_tunnel.ps1`：后台建立 Windows 到 DGX 的 API 转发。

Windows 访问入口：`http://127.0.0.1:18080`，API base URL 为
`http://127.0.0.1:18080/v1`。关闭转发或重启 Windows 后，在项目目录执行：

```powershell
.\scripts\start_qwen38_tunnel.ps1
```

转发通过本机 `127.0.0.1:7890` 的 HTTP 代理建立 SSH 连接。转发进程记录和日志位于
`test-results/qwen38-api-tunnel.json` 和 `test-results/qwen38-api-tunnel.stderr.log`。

PowerShell 中文 API 请求示例：

```powershell
$body = @{
    model = 'qwen38-27b-uncensored-q6-k'
    messages = @(@{ role = 'user'; content = '请用中文介绍一下你自己。' })
    max_tokens = 128
} | ConvertTo-Json -Depth 5
$response = Invoke-RestMethod -Uri 'http://127.0.0.1:18080/v1/chat/completions' `
    -Method Post -ContentType 'application/json; charset=utf-8' `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
$response.choices[0].message.content
```

部署完成后，远端服务管理命令为：

```powershell
$proxyOption = 'ProxyCommand="C:/Program Files/Git/mingw64/bin/connect.exe" -H 127.0.0.1:7890 %h %p'
ssh -T -o $proxyOption -o BatchMode=yes -o StrictHostKeyChecking=yes dgx python3 /home/jnu/work/qwen38-gguf-20261111/worker.py start
ssh -T -o $proxyOption -o BatchMode=yes -o StrictHostKeyChecking=yes dgx python3 /home/jnu/work/qwen38-gguf-20261111/worker.py stop
```

服务只在显式启动后运行，不设置开机自动启动。

## 临时文件清理

服务器旧的 `.range-download`、Hugging Face 下载缓存及 HTTP 上传分段已清理，
临时接收服务已停止、传输令牌已删除。Windows 端用于下载的反向 SSH 转发已关闭；
用于 API 的本地 SSH 转发仍在运行。

Windows 自动清理命令被执行策略拒绝，包括缩小范围后逐个删除临时文件的命令。
因此本机中转文件
`E:\ai\aigc\.cache\qwen38-transfer\Qwen3.8-27B-Uncensored-Q6_K.gguf`
仍保留，约占 20.9 GiB。该副本已不参与服务器推理；安装和 API 验收已完成。

## 工作台原生聊天接入（2026-11-14）

工作台首页及原影像表单增加 `http://127.0.0.1:8766/chat` 入口。
这里只提供连接状态、“连接并打开”、“独立打开”和“断开并释放”。
聊天区直接嵌入已部署的 llama.cpp 原生页面，`/qwen/` 透传原生 HTML、资源和 API。
没有另外实现聊天输入、消息展示、聊天历史或设置。原 UI 的对话与配置由原 UI 自行保存。

工作台复用现有 systemd socket activation 与远端 SSH 监督：

- 点击连接启用 socket；页面加载会启动轻量 router，模型仍按原生推理/加载请求加载。
- 900 秒闲置只根据推理或显式模型加载计算，正在回复时不回收。
  `/v1/models`、`/props`、原生模型状态 SSE、静态资源和本地状态查询不续期。
- 原生 HTTP/SSE 内容不改写，压缩资源保留编码；远端请求使用 `Connection: close`，
  不保留 API keep-alive。原 UI 的聊天参数与流式事件直接通过代理。
- 本地闲置到期停止 socket、代理、router、模型子进程，并关闭后台 SSE 与聊天 SSH。
  嵌入入口会收起已释放的原生页面，重新连接后可继续使用原 UI。
- 保留模型原生 900 秒 sleep 和 systemd proxy 的 900 秒 idle-exit。
  同一 SSH 连接承载 API 转发与远端监督；程序退出、SSH EOF/SIGHUP 或 120 秒
  没有心跳都会停止当前工作台拥有的全部 Qwen 单元。
- 取消或关闭原生推理连接时，上游连接关闭过程受到 shield 保护，避免重复取消中断清理。
  确认远端已回收后，关闭可能仍被 OpenSSH 保留的转发通道。
- 远端独占锁避免两个工作台互相停止服务。已经开启的独立 Qwen 入口会阻止接管，
  使用工作台之前先用上文 `worker.py stop` 关闭独立入口。
- 回收使用实际 systemd 状态和服务 MainPID 确认，无法确认会显示错误。
  socket 本身没有进程；backend 的 KillMode=control-group 覆盖全部模型子进程。

聊天与图片、视频、音乐和语音互斥切换。运行或归档中的任务阻止切换，
空闲环境先释放再启动下一个。代理请求最多 10 分钟，聊天仍为模型的单并发配置。
后台状态流重连不影响闲置计时。原 UI 可管理已部署模型的加载；工作台不提供模型下载或删除。
服务和 CUDA 清理可能在闲置阈值之后继续耗时数秒。

真实检查脚本 `scripts/chat_smoke.py` 使用独立本地端口 8767 和测试数据，
临时用 5 秒闲置阈值验证状态轮询时的回收，用 2 秒心跳阈值验证远端兜底。
结束后恢复 900 秒并停止 Qwen。记录保存在
`test-results/qwen-native-workbench-verification.json`。
运行前需断开正在使用的工作台聊天与独立 Qwen 入口。

2026-11-14 19:01（北京时间）真实 DGX 验收全部通过。临时 5 秒闲置配置下，
持续查询模型和属性时约 6.52 秒完成回收，原生 `/models/sse` 长连接同时关闭；
持续约 16.3 秒的冷启动流式回复没有被闲置计时中断。重新连接和多轮中文回复正常，
取消回复、客户端断开及回复中手动释放均已验证。强制退出本地工作台后约 1.88 秒
回收已加载模型；临时 2 秒心跳租约下约 2.61 秒完成远端兜底清理。
原生页面实际发送中文、停止回复及“断开并释放”也已通过浏览器验收。
结束后恢复正式 900 秒配置，三个 Qwen 单元均为 inactive、MainPID 为 0，
GPU 计算进程列表为空。原生界面截图位于
`test-results/qwen-native-workbench-browser.jpg`。
