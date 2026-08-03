# DeepSched

## Linux 本地开发

环境要求：Python 3.11+、Node.js 20+（需包含 Corepack）。

首次安装依赖：

```bash
./setup-linux.sh
```

需要通过本机代理下载依赖时：

```bash
DEEPSCHED_PROXY=http://127.0.0.1:7897 ./setup-linux.sh
```

启动带热更新的前后端：

```bash
./start-linux.sh
```

- 前端：`http://<本机 IP>:5889`（监听 `0.0.0.0`）
- 后端接口文档：http://127.0.0.1:8000/docs
- 本地数据：`server/cro_scheduler.db`（SQLite，首次启动自动创建）

停止服务时在启动终端按 `Ctrl+C`。前后端日志写入 `.runtime/logs/`。

## 本机正式运行

当前服务器使用本机 Python 虚拟环境、SQLite 和前端静态构建，不依赖 Docker 或 MySQL。首次运行仍先安装依赖：

```bash
./setup-linux.sh
```

启动正式模式前，先停止开发模式，再执行：

```bash
./start-production.sh
```

正式启动脚本会：

- 构建 `web/dist`；
- 强制使用 `ENVIRONMENT=production`；
- 默认使用 `server/cro_scheduler.db`；
- 在 `5889` 端口同时提供前端页面和 `/api/`；
- 将日志写入 `.runtime/logs/server/`；
- 关闭 Swagger、Redoc 和 OpenAPI 调试入口。

公网 Nginx 继续反向代理到本机 `5889` 端口。不要在公网环境使用 `start-linux.sh`。

如需临时更换监听地址或端口，可以使用：

```bash
DEEPSCHED_HOST=127.0.0.1 DEEPSCHED_PORT=5890 ./start-production.sh
```
