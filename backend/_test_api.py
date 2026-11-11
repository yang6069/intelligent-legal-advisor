# -*- coding: utf-8 -*-
"""
智法通本地 API 集成测试（临时脚本，执行后删除）。

覆盖:
    1. 健康检查
    2. 三角色登录 + 菜单权限差异
    3. RBAC 接口鉴权（403 场景）
    4. 智能问答（非流式）
    5. 合同审查（文本直审，多 Agent 工作流）
    6. MCP 工具发现/调用
    7. 数据分析接口
    8. 审计日志
"""
import requests
import time
import json

BASE = "http://127.0.0.1:28000"
passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
        print(f"  [PASS] {name}")
    else:
        failed.append((name, detail))
        print(f"  [FAIL] {name} -> {detail}")


def login(username, password):
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"username": username, "password": password})
    assert r.status_code == 200, f"登录失败 {username}: {r.text}"
    body = r.json()
    # 展平 token 到顶层，便于统一取用
    body["access_token"] = body["token"]
    return body


print("=== 1. 健康检查 ===")
r = requests.get(f"{BASE}/health", timeout=15)
check("GET /health", r.status_code == 200 and r.json()["status"] == "ok",
      r.text[:200])
print("  services:", r.json()["services"])

print("=== 2. 三角色登录与菜单 ===")
admin = login("admin", "admin123")
lawyer = login("lawyer", "lawyer123")
user = login("user", "user123456")

check("admin 登录返回 JWT", "access_token" in admin)
check("admin 菜单含用户管理(9项)",
      len(admin["menus"]) == 9 and any(m["code"] == "users" for m in admin["menus"]))
check("lawyer 菜单 6 项（无 users/audit/mcp）",
      len(lawyer["menus"]) == 6 and not any(m["code"] in ("users", "audit", "mcp")
                                            for m in lawyer["menus"]))
check("user 菜单 3 项（仅 chat/contract/profile）",
      len(user["menus"]) == 3 and {m["code"] for m in user["menus"]} ==
      {"chat", "contract", "profile"})

print("=== 3. RBAC 接口鉴权 ===")
h_user = {"Authorization": f"Bearer {user['access_token']}"}
h_lawyer = {"Authorization": f"Bearer {lawyer['access_token']}"}
h_admin = {"Authorization": f"Bearer {admin['access_token']}"}

r = requests.get(f"{BASE}/api/users", headers=h_user)
check("普通用户访问用户管理 -> 403", r.status_code == 403, f"{r.status_code}")
r = requests.get(f"{BASE}/api/knowledge/docs", headers=h_user)
check("普通用户访问知识库 -> 403", r.status_code == 403, f"{r.status_code}")
r = requests.get(f"{BASE}/api/mcp/tools", headers=h_lawyer)
check("法务负责人访问 MCP -> 403", r.status_code == 403, f"{r.status_code}")
r = requests.get(f"{BASE}/api/users", headers=h_admin)
check("admin 访问用户管理 -> 200", r.status_code == 200, f"{r.status_code}")
r = requests.get(f"{BASE}/api/qa/sessions", headers=h_user)
check("普通用户访问问答 -> 200", r.status_code == 200, f"{r.status_code}")

print("=== 4. 智能问答（RAG 全链路）===")
t0 = time.time()
r = requests.post(f"{BASE}/api/qa/ask", headers=h_user, timeout=300,
                  json={"question": "劳动合同中竞业限制条款有什么法律要求？"})
dur = time.time() - t0
body = r.json()
check("POST /api/qa/ask -> 200", r.status_code == 200, r.text[:300])
check("返回回答内容", len(body.get("answer", "")) > 50,
      body.get("answer", "")[:100])
check("返回法条引用", len(body.get("citations", [])) > 0, str(body.get("citations")))
check("返回置信度", 0 <= body.get("confidence", -1) <= 1, str(body.get("confidence")))
print(f"  问答耗时 {dur:.1f}s, 引用 {len(body.get('citations', []))} 条, "
      f"置信度 {body.get('confidence')}")

print("=== 5. 合同审查（多 Agent 工作流）===")
contract_text = """软件技术服务合同
甲方：华信科技有限公司
乙方：创达软件开发有限公司
第一条 服务内容：乙方为甲方开发企业管理系统，服务期12个月。
第二条 服务费用：合同总金额50万元，甲方应在签约后7日内支付全部款项。
第三条 单方解除权：甲方有权随时解除本合同而无需乙方同意，且不承担任何违约责任。
第四条 违约责任：乙方逾期交付的，每日按合同总额的百分之二支付违约金。
第五条 知识产权：全部源代码及知识产权归甲方所有。
第六条 保密义务：乙方对甲方商业秘密承担保密责任。
第七条 争议解决：因本合同产生的争议，须提交乙方所在地仲裁委员会仲裁。
第八条 最终解释权：本合同条款的最终解释权归甲方所有。"""
t0 = time.time()
r = requests.post(f"{BASE}/api/contract/review-text", headers=h_user, timeout=600,
                  json={"title": "测试-软件服务合同", "contract_text": contract_text})
dur = time.time() - t0
check("POST /api/contract/review-text -> 200", r.status_code == 200, r.text[:300])
cid = r.json().get("contract_id")
r2 = requests.get(f"{BASE}/api/contract/{cid}", headers=h_user)
detail = r2.json()
check("审查报告可查询", r2.status_code == 200 and detail.get("status") == "completed",
      f"{detail.get('status')} {detail.get('error_msg')}")
report = detail.get("report") or {}
check("报告含风险评分", 0 <= detail.get("risk_score", -1) <= 100,
      str(detail.get("risk_score")))
check("报告含三方意见结构",
      "primary_opinions" in json.dumps(report) or
      report.get("primary_risks") is not None or len(report) > 5,
      str(list(report.keys()))[:200])
print(f"  审查耗时 {dur:.1f}s, 风险分 {detail.get('risk_score')}, "
      f"等级 {detail.get('risk_level')}, 风险数 {detail.get('risk_count')}")

print("=== 6. MCP 工具 ===")
r = requests.get(f"{BASE}/api/mcp/tools", headers=h_admin)
check("工具发现 8 个", r.status_code == 200 and
      len(r.json()["tools"]) == 8, r.text[:200])
r = requests.post(f"{BASE}/api/mcp/tools/call", headers=h_admin, timeout=30,
                  json={"tool_name": "calculate_compensation",
                        "arguments": {"monthly_salary": 20000, "work_years": 3.5,
                                      "illegal_termination": True}})
check("赔偿金计算工具调用", r.status_code == 200 and
      r.json()["result"]["damages_2n"] == 160000.0, r.text[:300])
r = requests.post(f"{BASE}/api/mcp/tools/call", headers=h_admin, timeout=60,
                  json={"tool_name": "search_articles",
                        "arguments": {"query": "竞业限制"}})
check("法条检索工具调用", r.status_code == 200 and
      r.json()["result"]["total"] > 0, r.text[:300])

print("=== 7. 数据分析 ===")
r = requests.get(f"{BASE}/api/analytics/dashboard", headers=h_lawyer)
check("仪表盘统计", r.status_code == 200 and
      "knowledge" in r.json(), r.text[:200])
r = requests.get(f"{BASE}/api/analytics/hot-topics", headers=h_lawyer)
check("热点分析", r.status_code == 200, r.text[:200])
r = requests.get(f"{BASE}/api/analytics/clusters", headers=h_lawyer)
check("用户聚类", r.status_code == 200, r.text[:200])

print("=== 8. 审计日志 ===")
r = requests.get(f"{BASE}/api/audit/logs", headers=h_admin)
check("审计日志查询", r.status_code == 200 and
      r.json()["total"] > 0, r.text[:200])

print(f"\n{'=' * 50}")
print(f"通过: {len(passed)} / 失败: {len(failed)}")
for name, detail in failed:
    print(f"  FAIL: {name}: {detail[:150]}")
