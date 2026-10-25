# 部署指南

> [!WARNING]
> AmritaGO 处于 Alpha 阶段，存在不稳定性和潜在安全缺陷，不建议生产使用。应用目前默认只监听本机地址；不要为了远程访问而直接绑定公网或在防火墙开放端口。

## 使用 `uvx`

发布到 PyPI 后，可以不克隆仓库、不手动创建虚拟环境，直接运行：

```bash
uvx --from "amrita-go==0.1.0a1" amrita-go
```

将版本号换成实际发布的版本。`uvx` 会为命令准备临时工具环境；AmritaGO 的用户数据默认保存在用户目录下的 `.amrita`，不会跟随临时环境删除。`uvx` 是前台运行方式，关闭进程即停止服务。

若当前 Alpha 版本尚未发布到 PyPI，可从包含前端构建产物的本地仓库运行：

```bash
# 在仓库根目录执行；需要安装 uv、Bun，并按项目配置准备 AmritaCore 同级源码目录。
cd frontend
bun install
bun run build
cd ../backend
uvx --from . amrita-go
```

项目开发配置将 `amrita-core` 指向仓库外的 `../../AmritaCore` 源码目录，因此源码运行需要准备该同级目录。普通用户优先使用发布到 PyPI 的 wheel，不需要 AmritaCore 源码或 Bun。

### 设置数据和工作区目录

默认监听地址是 `127.0.0.1:8000`。可以在运行前设定数据目录和工作区的 `.amrita` 目录：

Linux/macOS：

```bash
export AMRITA_GO_DATA="$HOME/.amrita"
export AMRITA_GO_WORKSPACE="$PWD/.amrita"
uvx --from "amrita-go==0.1.0a1" amrita-go
```

Windows PowerShell：

```powershell
$env:AMRITA_GO_DATA = "$HOME\.amrita"
$env:AMRITA_GO_WORKSPACE = "$PWD\.amrita"
uvx --from "amrita-go==0.1.0a1" amrita-go
```

若不设置 `AMRITA_GO_WORKSPACE`，源码运行时默认使用仓库的 `.amrita`。用 `uvx` 运行时建议显式指定，避免依赖工具缓存目录作为工作区。

启动后可检查健康接口：

```text
http://127.0.0.1:8000/api/health
```

## 从源码启动（开发）

要求 Python `>=3.10,<3.15`、[uv](https://docs.astral.sh/uv/) 和 [Bun](https://bun.sh/)。

```bash
cd backend
uv sync --all-extras

cd ../frontend
bun install
bun run build

cd ../backend
uv run python -m app
```

前后端分离的开发模式：

```bash
# 终端一
cd frontend
bun install
bun run dev

# 终端二
cd backend
uv run python -m app
```

## 构建发布包

从仓库根目录先构建前端，再构建后端发行包：

```bash
cd frontend
bun install
bun run build
cd ../backend
uv build
```

wheel 会把前端文件打包到 `app/_frontend`；sdist 也包含 `frontend/dist`。若构建时找不到 `frontend/dist/index.html`，构建钩子会尝试用 Bun 自动构建，失败时会显式报错。使用预先构建好的其他前端目录时可设置 `AMRITA_GO_FRONTEND_SRC`。

## 网络与运行安全

- 默认只监听本机：`AMRITA_GO_HOST=127.0.0.1`。当前 Alpha 不应直接作为公网服务，也不要把绑定地址改为 `0.0.0.0` 后裸露在网络中。
- 仅在可信环境下运行。插件代码和 Agent 工具运行在当前用户权限下；插件不是沙箱。
- 不要把 API Key、`secret.key`、会话记录或 `.amrita` 数据目录提交到仓库。
- 需要远程访问时，应先自行评估风险并在可信网络边界内配置认证、TLS 和访问控制；本项目当前不能替代这些保护措施。
