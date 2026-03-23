# 微信消息通知助手 (WeChat Message Assistant)

基于 wechat-decrypt 项目实现的 Windows 微信消息监听和通知推送工具

## 功能特性

- ✅ 支持微信 4.x 版本
- ✅ 实时监听微信新消息
- ✅ Windows 10/11 Toast 通知推送
- ✅ 自动解密微信数据库
- ✅ 防抖动处理，避免重复通知

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 提取微信密钥

```bash
cd wechat-decrypt
python find_all_keys_windows.py
```

### 3. 运行程序

```bash
python wx_gui_notifier.py
```

## 项目结构

```
WxGuiNotifier/
├── wx_gui_notifier.py      # 主程序（GUI 界面）
├── wx_decrypt.py           # 微信数据库解密模块
├── requirements.txt        # Python 依赖包
├── wechat-decrypt/         # wechat-decrypt 子模块（密钥提取工具）
└── README.md              # 本说明文档
```

## 使用说明

1. **首次使用**：需要先提取微信密钥（步骤 2）
2. **启动程序**：运行主程序后开启服务开关
3. **配置选项**：
   - 消息防抖动时间：避免连续消息导致重复通知
   - 通知停留时间：Windows 通知显示时长
   - 数据缓存目录：存放解密后的临时数据库

## 注意事项

- 需要以管理员身份运行（获取微信密钥需要）
- 微信必须保持登录状态
- 仅支持 Windows 10/11 系统

## 已知问题

1. **用户名匹配问题**：部分消息的发送者名称显示不正确
   - 原因：微信 4.x 的会话表结构复杂，real_sender_id 与会话的映射关系需要进一步研究
   - 现状：联系人表使用 id 字段（整数）匹配 real_sender_id，但部分会话的发送者 ID 可能不是全局联系人 ID

## 技术栈

- **GUI 框架**: PyQt5 + QFluentWidgets
- **解密模块**: wechat-decrypt
- **文件监控**: watchdog
- **通知推送**: winotify

## 许可证

MIT License
