# 微信消息通知助手

一个面向微信 PC 4.x 的 Windows 桌面消息提醒工具。它通过读取并解密微信本地数据库，实时监听会话更新，并使用 Windows Toast 通知展示新消息提醒。

项目重点解决：

- 微信 PC 新消息弹窗提醒
- 联系人 / 群聊头像显示
- 消息免打扰过滤
- 公众号文章推送过滤
- 手动过滤联系人 / 群
- Windows 通知声音兜底

---
[img]https://free.boltp.com/2026/06/27/6a3fe18151555.webp[/img]

## 兼容性

| 项目 | 状态 |
| --- | --- |
| 已测试微信版本 | `4.1.7.33` |
| 理论支持微信版本 | 微信 PC `4.0+`，以 `xwechat_files/<wxid>/db_storage` 数据结构为前提 |
| 已验证系统 | Windows 10 |
| Windows 11 | 理论支持 |

### Windows 11 支持说明

本项目使用的是 Windows 标准桌面能力：

- PyQt5 桌面 GUI
- Windows Toast 通知
- `winotify`
- `winsound`
- 本地 SQLite 数据库读取
- 微信 PC 本地数据库解密

这些能力在 Windows 11 上仍然可用，因此**理论上支持 Windows 11**。  
但 Windows 11 的通知策略、专注助手、应用通知权限和声音策略可能更严格，如果出现无声音或不显示通知，请优先检查系统通知设置。

---

## 功能特性

### 实时消息提醒

- 监听微信 `session.db` 会话变化
- 默认 30ms 轮询检测
- 新消息通过 Windows Toast 弹窗提醒
- 群聊消息显示群名和发送者


### 免打扰过滤

支持自动识别常见免打扰字段：

- `mute_notification`
- `is_mute`
- `is_muted`
- `mute`
- `notification_on`
- `notify_status`
- `notifyflag`
- `notify_flag`
- `chatroom_notify`
- `chatroomnotify`
- `message_notice`

如果当前微信版本没有暴露稳定字段，可以使用前端「手动过滤」作为兜底。

### 公众号过滤

支持过滤：

- `gh_` 开头公众号
- `brandsessionholder`
- `officialaccounts`
- `mphelper`

其中 `brandsessionholder` 是微信 PC 常见的公众号聚合会话，会被直接过滤。

### 手动过滤

前端提供「手动过滤」面板：

- 可搜索已加载联系人 / 群聊
- 可搜索已弹窗提醒过的联系人 / 群
- 支持双击添加
- 支持手动输入 username
- 支持移除、清空、保存

手动过滤命中后不再弹窗，且独立于「过滤消息免打扰」开关。

---

## 快速开始

### 1. 安装依赖

建议使用干净的 Python 虚拟环境。

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

推荐 Python：

- Python 3.10 64-bit
- Python 3.11 64-bit

如果遇到 `python310.dll conflicts with this version of Python`，通常是全局环境里 `pywin32` 或 GUI 依赖 ABI 混乱。建议删除虚拟环境后重建，不要混用全局 site-packages。

### 2. 准备密钥文件

项目依赖 `all_keys.json` 中的数据库专用密钥。

至少需要包含：

```json
{
  "message\\message_0.db": {
    "enc_key": "..."
  },
  "contact\\contact.db": {
    "enc_key": "..."
  },
  "session\\session.db": {
    "enc_key": "..."
  }
}
```

> 不要把 `all_keys.json` 提交到 GitHub，也不要公开粘贴。它包含本机微信数据库解密密钥。

### 3. 配置数据库路径

配置文件位置：

- 源码运行：`utils/gui_config.json`
- 打包运行：`%LOCALAPPDATA%\WxGuiNotifier\gui_config.json`

可参考 `gui_config.example.json`：

```json
{
  "db_dir": "D:\\xwechat_files\\your_wxid\\db_storage",
  "keys_file": "all_keys.json",
  "wechat_process": "Weixin.exe",
  "gui": {
    "temp_dir": "wx_temp_data",
    "debounce_time_ms": 1000,
    "notify_duration_sec": 5,
    "filter_mute": true,
    "filter_official_article": true,
    "mute_usernames": [],
    "enable_notification_sound": true,
    "sound_alias": "SystemAsterisk"
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `db_dir` | 微信 `db_storage` 目录 |
| `keys_file` | `all_keys.json` 路径，支持绝对路径 |
| `temp_dir` | 解密后临时数据库缓存目录 |
| `filter_mute` | 是否过滤消息免打扰联系人 / 群 |
| `filter_official_article` | 是否过滤公众号文章推送 |
| `mute_usernames` | 手动过滤列表 |
| `enable_notification_sound` | 是否播放通知声音 |
| `sound_alias` | Windows 系统声音别名 |

### 4. 启动

```bash
python wx_gui_notifier.py
```

建议以管理员身份运行，便于读取微信进程信息和密钥。

---

## 手动过滤示例

推荐通过前端 UI 添加。

也可以直接写入配置：

```json
{
  "gui": {
    "mute_usernames": [
      "18336@chatroom",
      "wxid_xxx"
    ]
  }
}
```

也支持显示名匹配，例如：

```json
{
  "gui": {
    "mute_usernames": [
      "夏目友人帐"
    ]
  }
}
```

但显示名可能变更，稳定性不如真实 username。优先建议从 UI 候选列表添加。

---

## 通知声音

本项目同时使用：

- `winotify` Toast 声音
- `MessageBeep`
- `winsound.PlaySound`
- `winsound.Beep` 兜底

可选声音别名：

```text
SystemAsterisk
SystemExclamation
SystemNotification
SystemDefault
```

如果仍然无声音，请检查 Windows：

1. 系统音量
2. 应用音量混音器
3. 专注助手 / 请勿打扰
4. 设置 → 系统 → 通知
5. 是否允许通知播放声音
6. `微信消息` 这个通知来源是否被静音

---

## 打包

项目包含 PyInstaller 配置：

```bash
python build.py
```

或：

```bash
pyinstaller WxGuiNotifier.spec --noconfirm --clean
```

建议在干净虚拟环境中打包，避免全局 Python 依赖污染。

打包后请确认：

- `all_keys.json` 路径正确
- `gui_config.json` 中 `keys_file` 指向有效密钥文件
- 微信已登录
- 数据库路径指向当前账号的 `db_storage`

---

## 常见问题

### `file is not a database`

通常不是数据库损坏，而是使用了错误密钥。

请检查：

- `all_keys.json` 是否存在
- `keys_file` 是否指向正确路径
- 是否包含 `contact\\contact.db`
- 是否包含 `session\\session.db`
- 是否误用了 `message\\message_0.db` 的 key 解其它数据库

### 免打扰仍然弹窗

不同微信小版本字段差异较大。建议：

1. 打开「过滤消息免打扰」
2. 如果仍不生效，在「手动过滤」中搜索联系人 / 群并添加


### 弹窗没有声音

优先检查 Windows 通知和声音策略。Windows 11 尤其容易被系统通知策略静音。

---

## 安全说明

本项目会读取并解密本机微信数据库。请仅在自己的设备和账号上使用。

请勿公开以下文件：

- `all_keys.json`
- 解密后的数据库
- `wx_temp_data`
- 日志中包含的敏感路径或账号信息

---

## 项目结构

```text
.
├── wx_gui_notifier.py        # GUI 与消息监听主程序
├── core/
│   ├── wx_decrypt.py         # 微信环境与密钥加载
│   └── wechat_decrypt_core.py
├── utils/
│   ├── avatar_cache.py       # 头像缓存
│   ├── gui_config.py         # GUI 配置管理
│   └── auto_extract_keys.py
├── src/img/                  # 默认图标
├── gui_config.example.json
├── requirements.txt
├── build.py
└── WxGuiNotifier.spec
```

---

## Star History

<a href="https://www.star-history.com/?repos=gally16%2FLLM-Jailbreaking-Guide%2Cgally16%2Fwechat-message-assistant-windows&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=gally16/LLM-Jailbreaking-Guide%2Cgally16/wechat-message-assistant-windows&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=gally16/LLM-Jailbreaking-Guide%2Cgally16/wechat-message-assistant-windows&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=gally16/LLM-Jailbreaking-Guide%2Cgally16/wechat-message-assistant-windows&type=date&legend=top-left" />
 </picture>
</a>
