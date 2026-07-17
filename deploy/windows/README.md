# Windows 本地运行迁移包

这个目录用于把当前项目直接搬到 Windows 笔记本运行。建议把整个项目目录复制过去，包括 `.local-data`，这样管理员密码、通知配置、当前持仓、数据库都能保留。

## 需要先安装

1. Windows 10/11
2. Python 3.11 或 3.12，安装时勾选 `Add python.exe to PATH`
3. Git for Windows
4. PowerShell 5+，Windows 自带即可

## 复制项目

推荐放到这种短路径，避免中文目录和空格带来的路径问题：

```powershell
D:\niuone
```

可以直接把 Mac 上整个 `niuone` 文件夹拷过去。Mac 的 `.local-data/.venv` 不能在 Windows 里复用；Windows 主入口 `run.bat` 会自动创建 Windows 版 `.local-data\.venv`。

## 第一次初始化

在 Windows PowerShell 里进入项目根目录：

```powershell
cd D:\niuone
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\deploy\windows\01-setup.ps1
```

这个脚本会做这些事：

- 创建必要的数据目录
- 自动把 `.local-data\dashboard.env` 里的 Mac 路径改成 Windows 当前路径
- 保留你的管理员密码、API Key、通知 webhook、交易纪律等已有配置

虚拟环境创建和 `requirements.txt` 依赖安装由根目录的 `run.bat` 在首次启动时自动完成。

## 启动项目

```powershell
.\deploy\windows\02-start-dashboard.ps1
```

这个脚本内部调用的是项目根目录的 Windows 主入口：

```powershell
.\run.bat
```

启动后浏览器打开：

```text
http://127.0.0.1:8787
```

设置页：

```text
http://127.0.0.1:8787/admin
```

## 开机自动运行

确认手动启动正常后，再注册开机任务：

```powershell
.\deploy\windows\03-install-startup-task.ps1
```

以后 Windows 登录后会自动启动 NiuOne。取消开机自启：

```powershell
.\deploy\windows\04-uninstall-startup-task.ps1
```

## Windows 电源设置

长期跑通知时建议：

- 接通电源时进入睡眠：从不
- 合盖操作：不采取任何操作
- 网卡电源管理：取消“允许计算机关闭此设备以节约电源”
- 设置 Windows 更新活跃时间，避免盘中自动重启

## 常见问题

如果 PowerShell 不允许运行脚本：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

如果端口被占用，可以临时换端口：

```powershell
.\deploy\windows\02-start-dashboard.ps1 -Port 8788
```

你也可以不用 PowerShell，直接在 CMD 或双击运行根目录的：

```bat
run.bat
```

如果页面还显示旧数据，先确认 `.local-data\dashboard.env` 里的路径已经是 Windows 路径，然后重启脚本。
