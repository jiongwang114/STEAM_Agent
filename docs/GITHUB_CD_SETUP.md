# GitHub CD 配置

工作流：`.github/workflows/deploy.yml`。它只在 `main` 推送或手动触发时部署，并通过 SSH 在 Ubuntu 上更新源码、安装依赖、重启 systemd 和检查 `/health`、`/ready`。

在 GitHub 仓库的 `Settings -> Secrets and variables -> Actions` 添加以下 **Environment secrets**，环境名为 `production`：

- `DEPLOY_HOST`: `152.32.172.139`
- `DEPLOY_PORT`: `22`
- `DEPLOY_USER`: `ubuntu`
- `DEPLOY_PATH`: `/home/ubuntu/steam-agent`
- `DEPLOY_SSH_KEY`: 专用部署私钥全文；对应公钥只放在服务器 `/home/ubuntu/.ssh/authorized_keys`
- `DEPLOY_KNOWN_HOSTS`: 服务器公钥指纹对应的完整 `known_hosts` 行；不要在工作流中动态信任 `ssh-keyscan` 结果

服务器首次准备：创建 `/home/ubuntu/steam-agent`，放置服务器专用 `.env`，并确保 `ubuntu` 可以执行 `sudo systemctl`、`sudo install`、`sudo systemctl daemon-reload`、`sudo systemctl enable/restart`。工作流不会上传或覆盖 `.env`、SQLite 数据库、Chroma 数据和测试产物。

部署前应确认服务器已安装 Python 3、`python3 -m venv`、`curl` 和 `sudo`。首次部署成功后，后续合并到 `main` 会自动触发 CD；`workflow_dispatch` 可手动重跑。
