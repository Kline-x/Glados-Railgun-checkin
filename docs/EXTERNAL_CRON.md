# 可靠定时签到：外部 Cron + workflow_dispatch

## 结论

本仓库实测：

- `push` / 手动 **Run workflow**：**正常**
- GitHub `schedule`：**不可靠**（会延迟或整天不触发）

因此推荐用 **外部定时服务** 调用 GitHub API 触发 `workflow_dispatch`（和网页点 Run 相同，最稳）。

仓库里仍保留 `schedule` 作为尽力而为的备份，**不要依赖它**。

---

## 方案 A：cron-job.org（免费，推荐）

### 1. 创建 GitHub Token

1. 打开：https://github.com/settings/tokens
2. **Generate new token (classic)**
3. Note 随意，例如 `glados-dispatch`
4. 勾选权限：**`workflow`**（建议同时勾选 `repo` 若仓库以后改 private）
5. Generate 后 **复制 token**（只显示一次）

Fine-grained token 也可以：只选本仓库，Permissions → **Actions: Read and write**。

### 2. 在 cron-job.org 创建任务

1. 注册登录：https://cron-job.org
2. Create cronjob
3. 填写：

| 项 | 值 |
|----|-----|
| Title | Glados checkin |
| URL | `https://api.github.com/repos/Kline-x/Glados-Railgun-checkin/actions/workflows/gladosCheck.yml/dispatches` |
| Schedule | 每天 2 次，例如 04:05 和 10:05 **UTC**（= 新加坡 12:05 / 18:05） |
| Request method | **POST** |
| Enable | 开 |

4. **Request headers** 增加：

```
Accept: application/vnd.github+json
Authorization: Bearer 粘贴你的TOKEN
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

5. **Request body**：

```json
{"ref":"master"}
```

6. 保存后点 **Run now** 测一次。

### 3. 验证

打开：https://github.com/Kline-x/Glados-Railgun-checkin/actions  

应出现新的 run，Event 为 **workflow_dispatch**，Status 为 Success。

---

## 方案 B：本机 / 服务器 crontab

把 token 放到环境变量（不要写进仓库）：

```bash
export GH_DISPATCH_TOKEN=ghp_xxxx
```

Linux/macOS cron 示例（UTC）：

```cron
5 4,10 * * * curl -sS -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GH_DISPATCH_TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/Kline-x/Glados-Railgun-checkin/actions/workflows/gladosCheck.yml/dispatches \
  -d '{"ref":"master"}'
```

Windows 可用任务计划程序运行仓库里的 `scripts/trigger-checkin.ps1`。

---

## 方案 C：继续手动

Actions → **auto check** → **Run workflow** → Run。

---

## 安全注意

- Token 等同密码，**不要 commit 到 Git**
- 不要发到聊天/截图
- 泄露后立刻在 GitHub 撤销 token