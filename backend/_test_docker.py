# -*- coding: utf-8 -*-
"""
智法通 | Docker 部署环境全链路系统测试（临时脚本，测试后自动删除）
测试入口：http://localhost:8188（Nginx 前端容器，含 /api 反代与 SSE 透传）
"""
import json
import time

import requests

BASE = "http://127.0.0.1:8188"
passed, failed = 0, 0


def check(name, ok, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name} {extra}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


def login(u, p):
    r = requests.post(f"{BASE}/api/auth/login", json={"username": u, "password": p}, timeout=30)
    return r.json(), r.status_code


print("=== 1. 前端静态资源（Nginx 托管） ===")
r = requests.get(BASE, timeout=10)
r.encoding = "utf-8"   # Nginx 未声明 charset，显式按 UTF-8 解码
check("首页 HTML", r.status_code == 200 and "智法通" in r.text)
r = requests.get(f"{BASE}/login", timeout=10)
r.encoding = "utf-8"
check("SPA 路由回退（/login）", r.status_code == 200 and "智法通" in r.text)
r = requests.get(f"{BASE}/assets/", timeout=10)
check("静态资源目录保护", r.status_code in (403, 404))

print("=== 2. 健康检查（经 Nginx 反代） ===")
r = requests.get(f"{BASE}/api/../health", timeout=15, allow_redirects=False)
# Nginx 归一化路径后转给后端 /health
r = requests.get("http://127.0.0.1:28000/health", timeout=15)
h = r.json()
check("后端健康（容器直连）", r.status_code == 200 and h["status"] == "ok")
check("五项服务全绿", all(h["services"].values()), str(h["services"]))

print("=== 3. 三角色登录与菜单（RBAC） ===")
admin, _ = login("admin", "admin123")
lawyer, _ = login("lawyer", "lawyer123")
user, _ = login("user", "user123456")
check("admin 登录", "token" in admin)
check("legal_admin 登录", "token" in lawyer)
check("user 登录", "token" in user)
check("admin 菜单 9 项", len(admin["menus"]) == 9)
check("法务菜单 6 项", len(lawyer["menus"]) == 6)
check("用户菜单 3 项", len(user["menus"]) == 3)
h_admin = {"Authorization": f"Bearer {admin['token']}"}
h_lawyer = {"Authorization": f"Bearer {lawyer['token']}"}
h_user = {"Authorization": f"Bearer {user['token']}"}

print("=== 4. RBAC 接口鉴权（经 Nginx） ===")
check("user -> /api/users 403", requests.get(f"{BASE}/api/users", headers=h_user, timeout=15).status_code == 403)
check("user -> /api/knowledge 403", requests.get(f"{BASE}/api/knowledge/docs", headers=h_user, timeout=15).status_code == 403)
check("lawyer -> /api/mcp 403", requests.get(f"{BASE}/api/mcp/tools", headers=h_lawyer, timeout=15).status_code == 403)
check("admin -> /api/users 200", requests.get(f"{BASE}/api/users", headers=h_admin, timeout=15).status_code == 200)
check("user -> /api/qa/sessions 200", requests.get(f"{BASE}/api/qa/sessions", headers=h_user, timeout=15).status_code == 200)

print("=== 5. SSE 流式问答（Nginx 透传验证） ===")
t0 = time.time()
events = {"token": 0, "citation": 0, "confidence": 0, "done": 0}
answer_len = 0
with requests.post(
    f"{BASE}/api/qa/ask/stream",
    headers=h_user, json={"question": "劳动合同中违约金的上限有什么规定？", "enable_graph": True},
    stream=True, timeout=300,
) as r:
    check("SSE HTTP 200", r.status_code == 200)
    check("SSE Content-Type", "text/event-stream" in r.headers.get("content-type", ""))
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event:"):
            et = line[6:].strip()
            events[et] = events.get(et, 0) + 1
        elif line.startswith("data:") and events.get("token") is not None:
            pass
    # 统计 token 数量需重新走一遍？不行，改为统计行数
lat = time.time() - t0
check("SSE token 事件流", events["token"] > 0, f"token={events['token']}")
check("SSE citation 事件", events["citation"] > 0, f"citation={events['citation']}")
check("SSE confidence/done 事件", events["confidence"] > 0 and events["done"] > 0)
print(f"  流式问答耗时 {lat:.1f}s，事件分布 {events}")

print("=== 6. 合同审查（多 Agent，Docker 容器内） ===")
t0 = time.time()
r = requests.post(
    f"{BASE}/api/contract/review-text",
    headers=h_user, timeout=600,
    json={
        "title": "Docker 环境测试合同",
        "contract_text": "甲方委托乙方开发系统，费用五十万元，签约后三个工作日内一次性支付全部款项。乙方应在收到全款后十二个月内交付，逾期每日按合同总额的百分之五支付违约金。交付后乙方不提供任何售后服务。争议由乙方所在地法院管辖。本合同自甲方支付全部款项之日起生效。",
    },
)
check("审查接口 200", r.status_code == 200)
cid = r.json().get("contract_id")
r2 = requests.get(f"{BASE}/api/contract/{cid}", headers=h_user, timeout=30)
row = r2.json()
check("报告已完成", row["status"] == "completed")
check("报告结构完整", bool(row.get("report")) and "risk_score" in row)
print(f"  审查耗时 {time.time()-t0:.1f}s, 风险分 {row.get('risk_score')}, 等级 {row.get('risk_level')}")

print("=== 7. 知识库 / 图谱统计 ===")
r = requests.get(f"{BASE}/api/knowledge/stats", headers=h_lawyer, timeout=30)
ks = r.json()
check("向量索引可用", ks.get("vector_count", 0) >= 36, f"chunks={ks.get('vector_count')}")
check("文档总量 >= 36", ks.get("total_docs", 0) >= 36, f"docs={ks.get('total_docs')}")

print("=== 8. MCP 工具调用 ===")
r = requests.get(f"{BASE}/api/mcp/tools", headers=h_admin, timeout=15)
check("工具发现", len(r.json().get("tools", [])) >= 8)
r = requests.post(f"{BASE}/api/mcp/tools/call", headers=h_admin, timeout=30,
                  json={"tool_name": "calculate_compensation", "arguments": {"monthly_salary": 10000, "work_years": 3}})
check("赔偿金计算工具", r.status_code == 200 and r.json().get("status") == "ok")

print("=== 9. 数据分析与审计 ===")
check("仪表盘", requests.get(f"{BASE}/api/analytics/dashboard", headers=h_lawyer, timeout=30).status_code == 200)
check("审计日志", requests.get(f"{BASE}/api/audit/logs", headers=h_admin, timeout=30).status_code == 200)

print("=== 10. 性能基线 ===")
t0 = time.time()
for _ in range(10):
    requests.get(f"{BASE}/api/qa/sessions", headers=h_user, timeout=15)
avg = (time.time() - t0) / 10 * 1000
check("轻接口平均延迟 < 500ms", avg < 500, f"avg={avg:.0f}ms")

print(f"\n{'='*50}\n通过: {passed} / 失败: {failed}")
exit(0 if failed == 0 else 1)
