# Longshot

Longshot 是一个自托管的 iPhone 长截图工具。它不需要安装第二款长截图 App，也不依赖云端图像服务：先用 iOS 自带录屏捕获任意可滚动界面，再把录屏交给 Longshot 抽帧、对齐、去重并拼成一张长图。

适合：

- 微信、Telegram、Discord 等聊天记录
- 微博、小红书、社交信息流
- 设置页、订单页、商品详情页
- Safari 和其他可滚动页面

> iOS 不允许快捷指令替你在其他 App 里自动滑动，也不能启动或停止系统录屏。因此由你完成录屏和滚动，Longshot 负责后续的图像处理。

## 功能

- 从录屏自动选取关键帧，不要求逐张截图
- 估计滚动距离并直接按重叠区域拼接，减少重复列表错位
- 自动裁剪固定的顶部状态栏、导航栏和底部 Tab 栏
- 支持向上滚动后的阅读顺序恢复
- 支持 JPEG/PNG、三档输出宽度和自定义上下裁剪
- 支持视频、多张截图或图片 zip 上传
- 提供网页界面和 `multipart/form-data` API
- 可选生成 iOS 快捷指令，从照片分享菜单直接上传

## 工作原理

```text
iPhone 系统录屏
      │
      ▼
快捷指令 / Safari 上传
      │  multipart/form-data
      ▼
Longshot 服务器
  ├─ OpenCV 解码与抽帧
  ├─ 相位相关估计滚动量
  ├─ 选择有重叠的关键帧
  ├─ 裁剪固定栏并拼装
  └─ 返回 JPEG/PNG
      │
      ▼
照片相册 / 快速查看
```

服务不会自动替你滑动页面。一次录屏建议控制在 3–40 秒，并在滚动前后各停顿约半秒。

## 环境要求

- Python 3.10 或更高版本
- Linux、macOS 或其他可运行 Python/OpenCV 的服务器
- iPhone 与服务器可以互相访问：同一局域网、VPN 或 Tailscale 均可
- 若要生成签名快捷指令：macOS、可用的签名服务，或支持签名导出的 Cherri Playground

项目不需要系统 `ffmpeg`，视频解码由 OpenCV 完成。

## 快速开始

### 1. 获取代码

```bash
git clone https://github.com/SaiOogcn/iPhoneLongScreenShot.git
cd iPhoneLongScreenShot
```

### 2. 安装依赖

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
```

也可以运行 `./setup.sh`。如果系统里的 Python 命令不是 `python3`，可指定：

```bash
PYTHON_BIN=/path/to/python3 ./setup.sh
```

### 3. 启动服务

```bash
./start.sh
```

默认监听 `0.0.0.0:8787`。假设服务器的局域网地址是 `192.168.1.20`，在 iPhone 打开：

```text
http://192.168.1.20:8787
```

服务地址也可以由访问请求自动识别。如果处于反向代理、域名、端口映射或从服务器本机使用 `localhost` 访问等场景，建议显式设置：

```bash
export LONGSHOT_PUBLIC_URL="http://192.168.1.20:8787"
./start.sh
```

`LONGSHOT_PUBLIC_URL` 主要用于页面展示和二维码生成。快捷指令里的地址仍需在编译时写入，见下文。

### 4. 从 iPhone 访问

同一局域网：

1. 在服务器上查询局域网 IP，例如 `ip addr` 或 `ipconfig getifaddr en0`。
2. iPhone Safari 打开 `http://<服务器IP>:8787`。
3. 如果打不开，检查防火墙是否允许 TCP 8787。

使用 Tailscale 或其他 VPN：

1. 服务器和 iPhone 加入同一 tailnet/VPN。
2. 查询服务器在 VPN 中的地址，例如 `tailscale ip -4`。
3. iPhone 打开 `http://<VPN地址>:8787`。

不要在没有任何认证的情况下把端口直接暴露到公网。需要跨公网使用但没有 VPN 时，至少应在反向代理层增加 HTTPS 和身份认证。

## 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LONGSHOT_HOST` | `0.0.0.0` | 监听地址；只想本机访问可设为 `127.0.0.1` |
| `LONGSHOT_PORT` | `8787` | HTTP 端口 |
| `LONGSHOT_PUBLIC_URL` | 空 | 页面和二维码使用的规范外部地址；为空时使用当前请求的 Host |
| `LONGSHOT_WORK_DIR` | `/tmp/longshot-work` | 临时工作目录，需可写 |
| `LONGSHOT_MAX_UPLOAD_MB` | `400` | 单次 HTTP 请求上限，单位 MB |

示例：

```bash
export LONGSHOT_PUBLIC_URL="https://longshot.example.com"
export LONGSHOT_PORT=8787
export LONGSHOT_MAX_UPLOAD_MB=600
./start.sh
```

如果前面有 Nginx/Caddy，反向代理的请求体上限也必须不低于 `LONGSHOT_MAX_UPLOAD_MB`。

## 使用网页

1. 打开 Longshot 首页。
2. 选择录屏、2–24 张有重叠的截图，或包含截图的 zip。
3. 选择清晰度、格式、上下固定栏裁剪比例。
4. 点“开始生成长图”。
5. 长按预览图保存，或点“下载长图”。

清晰度对应输出宽度：

- `compact`：586 px
- `balanced`：780 px，默认
- `high`：1170 px

长录屏建议在网页端处理，便于看到完整错误信息和处理耗时。

## 使用快捷指令

### 方式 A：在 iPhone 上手动创建

在“快捷指令”App 新建快捷指令，依次添加：

1. **选择照片**
   - 筛选：视频
   - 选择多个：关闭
2. **获取 URL 内容**
   - URL：`http://<服务器IP>:8787/api/stitch`
   - 方法：`POST`
   - 请求体：`表单`
   - 新字段：键 `file`，类型 `文件`，值选上一步的照片
   - 可选字段：键 `quality`，类型 `文本`，值 `balanced`
3. **存储到照片相簿**
   - 输入：“URL 内容”
   - 相簿：“最近项目”
4. **快速查看**（可选）
   - 输入：“URL 内容”

如果希望从照片分享菜单直接运行，在快捷指令详情中打开“在共享表单中显示”，并把接收类型设为“媒体”。

### 方式 B：编译项目内的 Cherri 源码

`shortcut/longshot.cherri` 是快捷指令源码。先把占位地址替换成你的服务器地址：

```cherri
"http://YOUR_SERVER:8787/api/stitch"
```

安装 [Cherri](https://github.com/electrikmilk/cherri) 后编译：

```bash
cd shortcut
cherri longshot.cherri --skip-sign
```

生成的 `长截图_unsigned.shortcut` 还不能直接导入 iOS 26+，需要签名：

- macOS：使用系统自带的 `shortcuts sign`。
- Linux/Windows：使用兼容 [shortcut-signing-server](https://github.com/scaxyz/shortcut-signing-server) 的签名服务，或 Cherri 官方 Playground 的签名导出功能。
- RoutineHub HubSign 已停止服务，不要继续依赖它。

macOS 示例：

```bash
shortcuts sign \
  -i 长截图_unsigned.shortcut \
  -o longshot.shortcut \
  -m anyone
```

只把快捷指令文件发给签名服务，不要上传录屏或照片。签名完成后，把文件放到：

```text
shortcut/longshot.shortcut
```

服务首页会显示“一键导入”按钮，下载地址为：

```text
http://<服务器IP>:8787/longshot.shortcut
```

签名文件包含具体服务器地址，因此已在 `.gitignore` 中排除，不会随仓库分发。每个部署实例都应根据自己的地址重新生成。

### 导入后的日常流程

1. 控制中心开始录屏，等待倒计时结束。
2. 打开目标 App，先在页首停顿约半秒。
3. 平稳向下滚动，滚到页尾再停顿约半秒。
4. 停止录屏。
5. 在照片中分享录屏并运行长截图快捷指令，或直接运行快捷指令后选择视频。
6. 等待处理完成，长图会保存到“最近项目”并弹出快速查看。

Safari 如果只把快捷指令下载成文件，打开“文件”App 点一下 `.shortcut` 文件即可进入导入页。

## API

### `POST /api/stitch`

请求类型：`multipart/form-data`。

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `file` | 是 | 一个视频、多张图片，或一个图片 zip；该字段可重复 |
| `mode` | 否 | `auto`、`video`、`images`，默认 `auto` |
| `quality` | 否 | `compact`、`balanced`、`high`，默认 `balanced` |
| `format` | 否 | `jpg` 或 `png`，默认 `jpg` |
| `top_pct` | 否 | 顶部固定栏裁剪比例，默认 `18` |
| `bottom_pct` | 否 | 底部固定栏裁剪比例，默认 `12` |
| `jpeg_quality` | 否 | JPEG 质量 50–100，默认 `94` |

成功时直接返回 `image/jpeg` 或 `image/png`。响应头：

- `X-Longshot-Meta`：Base64 编码的 JSON 诊断信息
- `X-Longshot-Elapsed`：服务端处理耗时

失败时返回 JSON：

```json
{"ok": false, "error": "中文错误说明"}
```

示例：

```bash
curl -f http://192.168.1.20:8787/api/stitch \
  -F 'file=@screen-recording.mov' \
  -F 'mode=video' \
  -F 'quality=balanced' \
  -o longshot.jpg
```

### 其他端点

| 端点 | 说明 |
| --- | --- |
| `GET /` | 网页上传界面 |
| `GET /healthz` | 健康检查与忙碌状态 |
| `GET /qr.png` | 当前服务地址二维码 |
| `GET /longshot.shortcut` | 下载签名快捷指令（文件存在时） |

## 算法概要

1. OpenCV 解码视频，按约 7.5–8 fps 采样。
2. 裁掉固定的顶部和底部区域，避免状态栏、Tab 栏干扰匹配。
3. 使用相位相关，必要时辅以边缘图或模板匹配，估计相邻帧的垂直位移。
4. 找到稳定的滚动区间，选择最多约 25 个有足够重叠的关键帧。
5. 对高分辨率帧再次受约束对齐，按已知滚动量直接拼装重叠行。
6. 编码为 JPEG/PNG 返回。

静态截图使用同一套对齐与拼装核心。

## 使用限制

- 不能自动滚动，也不能自动开始/停止系统录屏；这是 iOS 沙盒限制。
- 录屏建议 3–40 秒，最长约 120 秒，单次约 25 屏内容。
- 相邻关键帧必须有视觉重叠。快速甩动到两屏完全不相交时，中间内容无法恢复。
- DRM、安全播放器和某些密码界面可能被系统录成黑屏。
- 聊天动画、悬浮按钮、自动播放视频和动态广告可能造成轻微残影或接缝。
- 输出高度上限约 30,000 px；内容过长时请降低清晰度或分段处理。
- HEIC/HEIF 图片依赖当前 OpenCV 构建是否带相应解码器；不确定时可先转成 JPEG/PNG。

## 隐私与安全

- 默认只监听 `0.0.0.0:8787`，没有内置账号系统。
- 上传文件写入临时目录，请求结束后删除；不写数据库。
- 不调用云端 OCR、大模型或第三方图像 API。
- 如果服务器在共享网络或公网可达，请使用防火墙、VPN、反向代理认证或额外访问令牌。
- 快捷指令签名服务只会收到快捷指令本身；用户录屏不会发给签名服务。

## 运维

```bash
./start.sh       # 后台启动，写入 .longshot.pid
./stop.sh        # 停止
./run.sh         # 前台运行，便于观察日志
tail -f longshot.log
```

`start.sh` 会先检查端口是否已有 HTTP 服务，避免重复实例。系统有 `setsid` 时使用它脱离终端，否则回退到 `nohup`。

修改代码后建议先跑测试：

```bash
.venv/bin/python -m pytest -q
```

测试会生成合成滚动录屏和截图，覆盖视频选帧、固定栏裁剪、重复列表拼接、HTTP 上传、快捷指令下载和动态服务地址。

## 故障排查

**iPhone 打不开页面**

- 确认手机和服务器在同一网络。
- 确认 `LONGSHOT_HOST` 不是仅 `127.0.0.1`。
- 检查防火墙和路由器隔离设置。
- 先在服务器上访问 `http://127.0.0.1:8787/healthz`。

**导入提示 “Importing unsigned shortcut files is not supported”**

快捷指令文件没有有效签名。重新使用 `shortcuts sign`、可信签名服务或 Playground 签名导出。

**快捷指令返回 JSON 而不是图片**

通常是服务报错、文件格式不对或处理超时。把同一段录屏上传到网页端，可以查看完整的中文错误信息。

**长图错位、重复或缺少内容**

- 降低滚动速度，让相邻画面保留更多重叠。
- 在页首和页尾各停顿约半秒。
- 不要中途切换到其他 App。
- 调整 `top_pct` / `bottom_pct` 后重试。

**启动时报端口已占用**

先停止已有实例，或换端口：

```bash
LONGSHOT_PORT=8788 ./start.sh
```

## 项目结构

```text
.
├── app.py                 # Flask 页面与 HTTP API
├── longshot_core.py       # 抽帧、对齐、裁剪与拼接
├── templates/index.html   # 网页界面
├── shortcut/
│   └── longshot.cherri    # 快捷指令源码
├── tests/                 # API 与算法测试
├── start.sh               # 后台启动
├── stop.sh                # 停止
├── run.sh                 # 前台运行
└── setup.sh               # 创建虚拟环境并安装依赖
```
