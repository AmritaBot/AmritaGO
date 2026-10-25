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

### 命令行参数与子命令

`amrita-go` 不带子命令时启动服务；参数在导入配置之前生效，因此会覆盖同名环境变量：

```bash
amrita-go --host 127.0.0.1 --port 9000 --data ~/.amrita
amrita-go --secret "$AMRITA_GO_SECRET"          # 等价于设置 AMRITA_GO_SECRET
amrita-go export latest -o chat.md              # 导出最近更新的会话
amrita-go export 3f2a9c -f json -o chat.json    # 按 ID 前缀或标题选择
amrita-go rotate-key                            # 交互确认后轮换凭据密钥
```

| 参数 | 覆盖的环境变量 | 说明 |
| --- | --- | --- |
| `--host` | `AMRITA_GO_HOST` | 监听地址 |
| `--port` | `AMRITA_GO_PORT` | 监听端口 |
| `--secret` | `AMRITA_GO_SECRET` | 凭据加密密钥（Base64 的 32 字节 Fernet key） |
| `--data` | `AMRITA_GO_DATA` | 数据目录 |
| `--debug` | `AMRITA_GO_DEBUG=1` | 开发者模式 |

`export` 支持 `-f markdown|json` 与 `-o <文件>`（`-o -` 写到标准输出）；会话参数接受完整 ID、唯一前缀、标题或 `latest`。

`rotate-key` 生成新密钥并用它重新加密所有 Provider 凭据：

- 当前密钥来自 `AMRITA_GO_SECRET` 时**拒绝执行**——环境变量在读取时永远优先，写到磁盘的新密钥不会被使用。
- 先把所有凭据重新加密到临时文件，全部成功后才替换密钥，避免中途失败导致凭据不可读。
- 密钥来自 `secret.key` 时，旧密钥会备份为 `secret.key.bak`（权限 0600），确认无误后可删除。
- 轮换后请重启正在运行的 AmritaGO 进程。

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

从仓库根目录运行打包脚本，它按顺序构建前端、打包后端，并在结束后清理临时文件：

```bash
cd frontend && bun install && cd ..
bash scripts/package.sh
```

脚本做三件事：

- 构建前端（`frontend/dist`），已构建好时可加 `--skip-frontend` 复用；
- 把仓库根目录的 `LICENSE` 复制到 `backend/LICENSE`，因为 hatchling 只打包 `backend/` 下的文件；构建结束（包括失败或 Ctrl-C）后自动删除这份副本，工作树不会留下重复文件；
- 打包后校验产物：必须含 `LICENSE` 与 `License-File` 元数据、必须含前端 `index.html`、且不得混入任何 `node_modules` 条目，否则构建失败。

wheel 会把前端文件打包到 `app/_frontend`；sdist 也包含 `frontend/dist`。若构建时找不到 `frontend/dist/index.html`，构建钩子会尝试用 Bun 自动构建，失败时会显式报错。使用预先构建好的其他前端目录时可设置 `AMRITA_GO_FRONTEND_SRC`。

前端默认不生成 source map（约占产物体积的三分之二）；需要调试生产构建时设置 `AMRITA_GO_SOURCEMAP=1` 再构建。

## 网络与运行安全

- 默认只监听本机：`AMRITA_GO_HOST=127.0.0.1`。当前 Alpha 不应直接作为公网服务，也不要把绑定地址改为 `0.0.0.0` 后裸露在网络中。
- 仅在可信环境下运行。插件代码和 Agent 工具运行在当前用户权限下；插件不是沙箱。
- 不要把 API Key、`secret.key`、会话记录或 `.amrita` 数据目录提交到仓库。
- 需要远程访问时，应先自行评估风险并在可信网络边界内配置认证、TLS 和访问控制；本项目当前不能替代这些保护措施。
