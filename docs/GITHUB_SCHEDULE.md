# 只用 GitHub 原生 schedule（不用外部 cron）

## 对比结论

| 仓库 | schedule |
|------|----------|
| 上游 Devilstore/Glados-Railgun-checkin | 正常，每天多次 Scheduled |
| 你的 fork Kline-x/Glados-Railgun-checkin | 几乎不触发（仅偶然延迟 1 次） |

YAML 本身没问题（已与上游对齐）。差异在于：fork 网络里的定时任务经常被 GitHub 降优先级或丢弃，上游原仓库则正常。

这仍是 GitHub Actions 官方 schedule，不是外部服务。

---

## 方案 1：继续用当前 fork（先做检查）

### A. 仓库设置（必须）

1. 打开：https://github.com/Kline-x/Glados-Railgun-checkin/settings/actions
2. Actions permissions → Allow all actions and reusable workflows
3. Workflow permissions → Read and write permissions → Save
4. 确认没有 Disable actions

### B. 重新启用 workflow

1. https://github.com/Kline-x/Glados-Railgun-checkin/actions
2. 左侧 auto check
3. 若有 Enable workflow，点启用
4. 再 Run workflow 手动跑通 1 次

### C. 给自己仓库点 Star

### D. 之后 24 小时内不要再改 cron

当前 cron（与上游一致）：

```yaml
schedule:
  - cron: '0 4,10 * * *'   # UTC 04:00/10:00 = 新加坡 12:00/18:00
```

到点后看 Actions 是否出现 Scheduled。GitHub 可能延迟数十分钟到数小时。

---

## 方案 2：新建非 Fork 仓库（仍纯 GitHub schedule，推荐）

上游证明：非 fork / 源仓库上 schedule 稳定。
fork 签到项目经常踩坑，把代码放到自己新建的空仓库（不要点 Fork）后，schedule 通常恢复正常。

### 步骤

1. 打开 https://github.com/new
   - Repository name：例如 glados-checkin
   - Public
   - 不要勾选 README / license（空库）
   - 不要从别的模板 fork
   - Create repository

2. 把本仓库代码推到新库：

```powershell
cd E:\code\AI\codex\Glados-Railgun-checkin
git remote rename origin fork-origin
git remote add origin https://github.com/Kline-x/glados-checkin.git
git push -u origin master
```

（把 glados-checkin 换成你新建的仓库名）

3. 在新仓库配置相同 Secrets：
   Settings → Secrets and variables → Actions
   - GLADOS_COOKIES（必填）
   - 其他可选：PUSHDEER_SENDKEY、GLADOS_EXCHANGE_PLAN 等

4. Actions 页 Enable workflows，手动 Run 一次确认成功

5. 不要再改 .github/workflows/gladosCheck.yml 里的 cron
   等到新加坡 12:00 或 18:00 后看是否出现 Scheduled

6. 旧 fork 可 archive 或不管

---

## 为什么坚持按 GitHub 的来时更推荐方案 2

- 触发器仍是官方 on.schedule + cron
- runner 仍是 ubuntu-latest GitHub-hosted
- 不引入 cron-job.org / 自建机器
- 与上游可跑通的形态一致（非 fork 网络）

方案 1 可以试，但在本 fork 上已多次错过 UTC 槽位；方案 2 是同机制下成功率更高的做法。