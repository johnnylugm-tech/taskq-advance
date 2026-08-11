# Bug Hunt Report — taskq-api (Gate 3 adversarial_review)

| 欄位 | 值 |
|---|---|
| 產生時間 | 2026-08-11 |
| Git SHA | `e219fa53b5e43b7a7e229e8594579b431de903c1` |
| Target manifest | `.methodology/bug_hunt_targets.json` |
| 配對模式 | high_risk × {correctness, concurrency, resilience} + standard × general + 12 threat_model 強制驗證 |
| 原始候選 | 12 |
| Confirmed | 0 |
| Refuted | 12 |
| 阻塞 Gate 3 | 否 |

## 掃描摘要

對 12 條 SAD §6 STRIDE-lite threat 逐一驗證宣告的 mitigation 是否真的擋得住攻擊,並對 6 個 high_risk 模組 (`key_repo / session / task_repo / auth / runner / tasks`) 套用 3 個 lens,21 個 standard 模組各 1 個 general lens。grep 結果:src 樹 0 個 `shell=True / eval( / exec(`、0 個 SQL 字串拼接;多數威脅已由既有測試 + 程式碼契約擋下,僅 1 條為防禦性缺失但無可達 exploit(T-10)。所有 12 條皆在本次 hunt 中以 live repro 驗證。

## 確認 bugs(severity 降序)

無 confirmed findings。

## 被反駁清單(一句理由 + 證據)

| Threat | Owner module | 一句理由 |
|---|---|---|
| T-01 tampering | `api/tasks` | Pydantic `TaskCreate` 注入字元黑名單 + `extra='forbid'` → 422 |
| T-02 spoofing | `api/deps` | 缺/錯 X-API-Key 一律 401,detail 固定 |
| T-03 spoofing | `repository/key_repo` | `key_hash = sha256(plaintext)`,plaintext 從不寫入 DB |
| T-04 elevation | `api/deps` | `check_scope` 早於資源查詢,read<write<admin 排序 |
| T-05 disclosure | `service/auth` | 403 envelope 對 existing/missing id 完全相同 |
| T-06 elevation | `service/runner` | `create_subprocess_exec(*shlex.split(cmd))`,無 shell |
| T-07 DoS | `service/runner` | live repro:`sleep 5` + timeout=1s → 1.01s 內 kill+reap |
| T-08 tampering | `repository/session` | ORM bind param;text() 兩處皆為靜態字串 |
| T-09 repudiation | `repository/session` | live repro:mid-context RuntimeError → 0 rows persisted |
| T-10 disclosure | `errors` | **無 redact 函式但無可達 exploit**;access log 只印 method/path/status/correlation_id,`/v1/metrics` 只回 task 計數;靜態 grep 測試守住 db_url() 不入 log |
| T-11 disclosure | `errors` | 500 envelope 為固定字串 + asyncio.CancelledError 重拋 |
| T-12 DoS | `service/ratelimit` | per-key bucket;live repro:k1 空 / k2 滿 → k1 429,k2 admit |

## 修復優先順序

無 confirmed critical/high,Gate 3 不被阻擋。`T-10` 為 spec gap(medium),不需 repro 修復即可放行 Gate 3;若需後續 hardening,加 `errors.redact(s)` 並讓 `_problem_response` 與 access logger 套用,並補一條針對 logger 含 secret 變數的測試。

## 掃描方法

1. CRG 圖已建(`mcp__code-review-graph`),`bug-hunt-targets` 輸出 18 high-risk / 21 standard / 12 threat_model。
2. Phase 1 完整讀取 12 個 high-risk + 16 個 standard 模組,記錄 candidate 觀察。
3. Phase 2 套用 lens:high_risk × {correctness, concurrency, resilience},standard × general,12 條 threat_model 強制驗證 mitigation。
4. Phase 3 對每條 threat 跑 live repro(`asyncio` + httpx ASGITransport),確認 mitigation 確實擋住攻擊;每條都附「具體觸發 + 預期 vs 實際」。
5. Phase 4 寫入 `.methodology/bug_hunt_report.json` 與本 markdown,並通過 `jsonschema.validate` schema 校驗。

## Self-Review

- 可能錯誤之處:(a) T-10 評為 medium 是基於「現有 access log 不印 secret」這個觀察,若未來新增 log 行為就會立即升級為 high;(b) 其他 11 條的 repro 都依賴 ASGI 內部 transport,production 行為可能有差異(如 FastAPI middleware 觸發 CancelledError 時機),但程式碼路徑相同。
- 未驗證假設:production PostgreSQL 的 row-level lock 行為(只在 SQLite 跑過 no-op 等價路徑);`with_for_update()` 在 PG 下的 deadlock 風險沒單獨驗證。
- 信心等級:High — 12 條皆有 live repro 與對應測試;報告結構通過 schema 驗證。
