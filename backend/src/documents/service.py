# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 文书起草服务
==================================================================
模块职责：
    1. 文书草稿 CRUD（全部角色可用，隔离权限同 qa_sessions）；
    2. 基于内置模板的文书自动起草（LLM 驱动，支持起诉状/答辩状/
       代理词/劳动合同/租赁合同/借条等常用法律文书）；
    3. 多格式导出：Word(.docx) / Markdown(.md) / 纯文本(.txt)；
    4. 字数统计、AI 起草历史、模板元数据管理。

内置模板清单（TEMPLATE_CATALOG）：
    每种模板提供标准字段（prompt 片段）供 LLM 填空，
    用户可先按模板起草，再手动微调后定稿导出。
==================================================================
"""
import io
import json
import logging
import re
from datetime import datetime

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from src import config, database
from src.audit.service import log_audit

logger = logging.getLogger("zhifatong.documents")


# ==================================================================
# 一、文书类型定义（业务字典，前端唯一数据源）
# ==================================================================
DOC_TYPES = [
    {"value": "complaint", "label": "民事起诉状", "icon": "Document"},
    {"value": "answer", "label": "民事答辩状", "icon": "Edit"},
    {"value": "proxy_statement", "label": "代理词", "icon": "Memo"},
    {"value": "labor_contract", "label": "劳动合同", "icon": "Calendar"},
    {"value": "lease_contract", "label": "房屋租赁合同", "icon": "OfficeBuilding"},
    {"value": "loan_note", "label": "借条 / 欠条", "icon": "Wallet"},
    {"value": "will", "label": "遗嘱", "icon": "CollectionTag"},
    {"value": "demand_letter", "label": "律师函 / 催告函", "icon": "Message"},
    {"value": "custom", "label": "自定义文书", "icon": "More"},
]


# ==================================================================
# 二、内置起草模板（LLM prompt 片段骨架）
# ==================================================================
TEMPLATE_CATALOG: dict[str, str] = {
    "complaint": """# 民事起诉状

原告：
    姓名：{plaintiff_name}
    性别：{plaintiff_gender}
    出生日期：{plaintiff_birth}
    住址：{plaintiff_addr}
    联系电话：{plaintiff_phone}
被告：
    姓名：{defendant_name}
    住址：{defendant_addr}
    联系电话：{defendant_phone}

## 诉讼请求
1. {claim_1}
2. {claim_2}
3. 诉讼费用由被告承担。

## 事实与理由
{facts_reasons}

## 此致
{court_name}

具状人：{plaintiff_name}
{date}""",

    "answer": """# 民事答辩状

答辩人（被告）：{defendant_name}
被答辩人（原告）：{plaintiff_name}

## 答辩请求
{defense_request}

## 事实与理由
{defense_reason}

## 此致
{court_name}

答辩人：{defendant_name}
{date}""",

    "proxy_statement": """# 代理词

代理律师：{lawyer_name}
代理案件：{case_info}

尊敬的审判长、审判员：

{opening}

## 一、{point_1_title}
{point_1_content}

## 二、{point_2_title}
{point_2_content}

## 三、{point_3_title}
{point_3_content}

{closing}

此致
{court_name}

代理律师：{lawyer_name}
{date}""",

    "labor_contract": """# 劳动合同书

甲方（用人单位）：{employer_name}
乙方（劳动者）：{employee_name}

根据《中华人民共和国劳动法》《中华人民共和国劳动合同法》及相关
法律法规，甲乙双方在平等自愿、协商一致的基础上，订立本劳动合同。

## 第一条 合同期限
{contract_term}

## 第二条 工作内容与工作地点
{work_content}

## 第三条 工作时间与休息休假
{work_time}

## 第四条 劳动报酬
{salary}

## 第五条 社会保险与福利待遇
{social_insurance}

## 第六条 劳动保护、劳动条件和职业危害防护
{labor_protection}

## 第七条 合同的解除与终止
{termination}

## 第八条 违约责任
{breach}

## 第九条 争议解决
{dispute}

甲方（盖章）：{employer_name}
乙方（签字）：{employee_name}
{date}""",

    "lease_contract": """# 房屋租赁合同

出租方（甲方）：{lessor}
承租方（乙方）：{lessee}

## 第一条 租赁房屋
{house_info}

## 第二条 租赁期限
{lease_term}

## 第三条 租金及支付方式
{rent_info}

## 第四条 押金
{deposit}

## 第五条 房屋使用与维修
{usage_maintenance}

## 第六条 合同变更、解除与终止
{termination}

## 第七条 违约责任
{breach}

## 第八条 争议解决
{dispute}

甲方（签字）：{lessor}
乙方（签字）：{lessee}
{date}""",

    "loan_note": """# 借条

本人（借款人）{borrower_name}，身份证号 {borrower_id}，因 {purpose}
向（出借人）{lender_name}，身份证号 {lender_id}，借款人民币
**{amount} 元整**（￥{amount_num}.00）。

借款期限：自 {borrow_date} 起至 {repay_date} 止。

借款利息：年利率 {interest_rate}%，到期一次性还本付息。

如借款人逾期未还，自愿承担逾期利息及出借人为实现债权产生的
律师费、诉讼费等全部费用。

借款人（签字按手印）：{borrower_name}
{sign_date}""",

    "will": """# 遗嘱

立遗嘱人：{testator_name}，{testator_gender}，身份证号 {testator_id}，
住址 {testator_addr}。

本人在立遗嘱时神智清楚，具有完全民事行为能力。为妥善处理本人
身后财产，特立此遗嘱：

## 一、遗产范围
{estate}

## 二、遗产分配
{distribution}

## 三、遗嘱执行人
{executor}

## 四、其他事项
{other}

立遗嘱人（签字）：{testator_name}
{sign_date}
代书人：{witness_1}
见证人：{witness_2}""",

    "demand_letter": """# 律师函

致：{recipient}

发函律师：{lawyer_name}
委托方：{client}

## 事由
{subject}

## 事实陈述
{facts}

## 法律分析
{legal_analysis}

## 我方要求
{demand}

## 法律后果告知
{consequence}

请贵方于 {deadline} 前与我方委托方协商解决，或直接履行上述要求。
逾期我方将采取包括但不限于诉讼在内的一切合法手段维护委托方合法权益。

特此函告。

发函律师（签章）：{lawyer_name}
{date}""",
}


def list_templates() -> list[dict]:
    """返回所有模板的元信息（供前端下拉选择）。"""
    return [
        {
            "template_id": t["value"],
            "label": t["label"],
            "icon": t["icon"],
            "has_template": t["value"] in TEMPLATE_CATALOG,
        }
        for t in DOC_TYPES
    ]


# ==================================================================
# 二.5、模板占位符中文对照表
#      模板骨架内部的 {snake_case} 是给 LLM 看的技术字段，
#      直接展示给用户会看不懂（如 {plaintiff_gender}）。
#      新建草稿预填模板时，统一转换为「【请填写：中文说明】」。
# ==================================================================
PLACEHOLDER_LABELS: dict[str, str] = {
    # --- 起诉状 / 答辩状 ---
    "plaintiff_name": "原告姓名", "plaintiff_gender": "性别（男/女）",
    "plaintiff_birth": "出生日期", "plaintiff_addr": "原告住址",
    "plaintiff_phone": "原告联系电话",
    "defendant_name": "被告姓名", "defendant_addr": "被告住址",
    "defendant_phone": "被告联系电话",
    "claim_1": "第一条诉讼请求", "claim_2": "第二条诉讼请求",
    "facts_reasons": "事实与理由（按时间顺序写明纠纷经过）",
    "court_name": "受诉法院名称（如：××市××区人民法院）",
    "date": "签署日期",
    "defense_request": "答辩请求", "defense_reason": "答辩的事实与理由",
    # --- 代理词 ---
    "lawyer_name": "律师姓名", "case_info": "代理案件名称/案号",
    "opening": "开场陈述", "point_1_title": "论点一标题",
    "point_1_content": "论点一内容", "point_2_title": "论点二标题",
    "point_2_content": "论点二内容", "point_3_title": "论点三标题",
    "point_3_content": "论点三内容", "closing": "总结陈词",
    # --- 劳动合同 ---
    "employer_name": "用人单位名称", "employee_name": "劳动者姓名",
    "contract_term": "合同期限（起止日期）",
    "work_content": "工作内容与工作地点",
    "work_time": "工作时间与休息休假安排",
    "salary": "劳动报酬（工资标准、发放方式与日期）",
    "social_insurance": "社会保险与福利待遇",
    "labor_protection": "劳动保护与职业危害防护",
    "termination": "合同的解除与终止条件",
    "breach": "违约责任", "dispute": "争议解决方式",
    # --- 租赁合同 ---
    "lessor": "出租方（甲方）名称", "lessee": "承租方（乙方）名称",
    "house_info": "房屋坐落、面积、附属设施",
    "lease_term": "租赁期限（起止日期）",
    "rent_info": "租金标准与支付方式",
    "deposit": "押金金额与退还条件",
    "usage_maintenance": "房屋使用要求与维修责任",
    # --- 借条 ---
    "borrower_name": "借款人姓名", "borrower_id": "借款人身份证号",
    "purpose": "借款用途", "lender_name": "出借人姓名",
    "lender_id": "出借人身份证号", "amount": "借款金额（数字）",
    "amount_num": "大写金额", "borrow_date": "借款日期",
    "repay_date": "还款日期", "interest_rate": "年利率",
    "sign_date": "签署日期",
    # --- 遗嘱 ---
    "testator_name": "立遗嘱人姓名", "testator_gender": "性别",
    "testator_id": "身份证号", "testator_addr": "住址",
    "estate": "遗产范围（逐项列明财产）",
    "distribution": "遗产分配方案（谁继承什么、份额多少）",
    "executor": "遗嘱执行人", "other": "其他需要说明的事项",
    "witness_1": "代书人姓名", "witness_2": "见证人姓名",
    # --- 律师函 ---
    "recipient": "收函方（对方单位/个人）", "client": "委托方名称",
    "subject": "致函事由", "facts": "事实经过陈述",
    "legal_analysis": "法律依据分析", "demand": "我方具体要求",
    "consequence": "逾期不履行的法律后果",
    "deadline": "履行期限（如：收函后 7 日内）",
}


def _user_fill_template(template: str) -> str:
    """
    将模板骨架中的 {snake_case} 技术占位符转换为
    用户可读的「【请填写：中文说明】」格式。

    示例：性别：{plaintiff_gender}
          -> 性别：【请填写：性别（男/女）】
    """
    def _replace(m: re.Match) -> str:
        key = m.group(1)
        label = PLACEHOLDER_LABELS.get(key)
        if label:
            return f"【请填写：{label}】"
        # 未知键兜底：去掉下划线转空格，仍保持中文引导
        return f"【请填写：{key.replace('_', ' ')}】"

    return re.sub(r"\{([a-zA-Z0-9_]+)\}", _replace, template)


# ==================================================================
# 三、草稿 CRUD
# ==================================================================
def _own_doc(user: dict, doc_id: int) -> dict:
    """校验文书归属（多用户数据隔离）。"""
    row = database.query_one(
        "SELECT * FROM draft_documents WHERE id = %s", (doc_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="文书不存在")
    if row["user_id"] != user["user_id"]:
        logger.warning("越权访问: 用户 %s 试图访问文书 #%d",
                       user["username"], doc_id)
        raise HTTPException(status_code=403, detail="无权访问该文书")
    return row


def list_drafts(user: dict, doc_type: str = "", keyword: str = "") -> dict:
    """列出我的文书草稿（按更新时间倒序）。"""
    where = "WHERE d.user_id = %s"
    args: list = [user["user_id"]]
    if doc_type:
        where += " AND d.doc_type = %s"
        args.append(doc_type)
    if keyword:
        where += " AND (d.title LIKE %s OR d.content LIKE %s)"
        args += [f"%{keyword}%", f"%{keyword}%"]
    args_tuple = tuple(args)
    total = database.query_one(
        f"SELECT COUNT(*) AS c FROM draft_documents d {where}", args_tuple)["c"]
    rows = database.query_all(
        f"SELECT d.id, d.title, d.doc_type, d.template_id, d.status, "
        f"d.word_count, d.created_at, d.updated_at "
        f"FROM draft_documents d {where} "
        f"ORDER BY d.updated_at DESC LIMIT 200", args_tuple)
    return {"total": total, "items": rows}


def get_draft(user: dict, doc_id: int) -> dict:
    """获取文书详情（含完整 content / metadata）。"""
    row = _own_doc(user, doc_id)
    row["metadata"] = _safe_json(row.get("metadata"))
    row["ai_params"] = _safe_json(row.get("ai_params"))
    return row


def create_draft(user: dict, title: str, doc_type: str,
                 template_id: str = "", metadata: dict | None = None) -> dict:
    """
    新建草稿。

    设计原则（修复 2026-09-23 用户反馈）：
        - 用户选择"使用模板"时，预填模板骨架的**中文可读版**：
          {plaintiff_gender} 等英文字段已转换为
          【请填写：性别（男/女）】，普通用户看得懂、可直接填空；
        - 不选模板则初始 content 留空，由用户自由编写或交给 AI 起草。
    """
    # 选了模板 -> 预填中文可读骨架；否则留空
    content = ""
    if template_id and template_id in TEMPLATE_CATALOG:
        content = _user_fill_template(TEMPLATE_CATALOG[template_id])
    doc_id = database.insert_return_id(
        "INSERT INTO draft_documents "
        "(user_id, title, doc_type, template_id, content, metadata, word_count) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (user["user_id"], title or "未命名文书", doc_type,
         template_id, content,
         json.dumps(metadata or {}, ensure_ascii=False),
         _count_words(content)))
    log_audit(user["user_id"], user["username"], "DOC_CREATE", "documents",
              f"新建文书草稿 #{doc_id}（{title}）", None)
    return {"id": doc_id, "title": title, "doc_type": doc_type,
            "content": content}


def update_draft(user: dict, doc_id: int, **kwargs) -> dict:
    """更新文书草稿（content / title / metadata / status）。"""
    row = _own_doc(user, doc_id)
    fields, args = [], []
    for k in ("title", "content", "status"):
        if k in kwargs:
            fields.append(f"{k} = %s")
            args.append(kwargs[k])
    if "metadata" in kwargs:
        fields.append("metadata = %s")
        args.append(json.dumps(kwargs["metadata"] or {}, ensure_ascii=False))
    if "ai_prompt" in kwargs:
        fields.append("ai_prompt = %s")
        args.append(kwargs["ai_prompt"])
    if kwargs.get("content") is not None:
        fields.append("word_count = %s")
        args.append(_count_words(kwargs["content"]))
    if not fields:
        return row
    args.append(doc_id)
    database.execute(
        f"UPDATE draft_documents SET {', '.join(fields)} WHERE id = %s",
        tuple(args))
    log_audit(user["user_id"], user["username"], "DOC_UPDATE", "documents",
              f"更新文书 #{doc_id}", None)
    return get_draft(user, doc_id)


def delete_draft(user: dict, doc_id: int) -> None:
    """删除文书草稿。"""
    _own_doc(user, doc_id)
    database.execute("DELETE FROM draft_documents WHERE id = %s", (doc_id,))
    log_audit(user["user_id"], user["username"], "DOC_DELETE", "documents",
              f"删除文书 #{doc_id}", None)


# ==================================================================
# 四、AI 起草（LLM 驱动）
# ==================================================================
async def ai_draft(user: dict, doc_type: str, ai_prompt: str,
                   template_id: str = "",
                   metadata: dict | None = None) -> dict:
    """
    调用 LLM 生成一份完整的法律文书草案。

    参数:
        doc_type: 文书类型（对应 DOC_TYPES.value）
        ai_prompt: 用户提供的起草要点 / 事实描述
        template_id: 使用的模板 ID（为空则用 doc_type 查）
        metadata: 结构化元数据（当事人、金额等）

    返回:
        新建草稿的详情 dict（含 content）

    修复记录（2026-09-23）:
        1. metadata 为 None 时 metadata.get("title") 崩溃（AttributeError
           -> 500 服务器内部错误），已改为 (metadata or {})；
        2. 增加 LLM 输出质量检测：小参数模型偶发输出
           <[MASK_xxx]>/[PAD]/[BOS] 等退化乱码，检测到后自动重试一次，
           仍失败则返回友好错误而非保存乱码。
    """
    from src.core.llm_client import llm_client

    template_id = template_id or doc_type
    template_content = TEMPLATE_CATALOG.get(template_id, "")
    metadata = metadata or {}

    system_prompt = (
        "你是资深执业律师，擅长起草各类法律文书。请严格依据"
        "用户提供的事实与要求，起草一份结构完整、法律用语规范、"
        "可直接用于实务的法律文书。"
        "务必使用 Markdown 格式输出，各级标题用 # 分级。"
        "不要输出任何解释、引言或注释，直接输出文书正文。"
    )

    # 组装用户提示词
    parts = []
    if template_content:
        parts.append(
            f"【参考模板骨架】\n以下为标准模板，你可参考此结构组织内容，"
            f"不必机械照搬占位符：\n\n{template_content}")
    meta_str = "\n".join(f"- {k}: {v}" for k, v in metadata.items() if v)
    if meta_str:
        parts.append(f"【结构化已知信息】\n{meta_str}")
    if ai_prompt:
        parts.append(f"【起草要点 / 事实描述】\n{ai_prompt}")
    else:
        parts.append("【起草要点】\n请为我起草一份完整的法律文书。")
    parts.append(f"\n【文书类型】{_label_of(doc_type)}")

    user_prompt = "\n\n".join(parts)
    content = await _generate_doc_with_retry(
        llm_client.generate, user_prompt, system_prompt)

    # 创建草稿
    title = metadata.get("title") or (ai_prompt[:30] or _label_of(doc_type))
    doc = create_draft(
        user, title=title, doc_type=doc_type,
        template_id=template_id, metadata=metadata)
    update_draft(user, doc["id"], content=content,
                 ai_prompt=ai_prompt, status="ai_generated")

    log_audit(user["user_id"], user["username"], "DOC_AI_DRAFT", "documents",
              f"AI 起草 {_label_of(doc_type)}", None)

    return {
        "id": doc["id"],
        "title": title,
        "doc_type": doc_type,
        "content": content,
        "word_count": _count_words(content),
    }


async def ai_rewrite(user: dict, doc_id: int, instruction: str = "") -> dict:
    """
    AI 重写：基于当前草稿内容整体润色/改写，更新到原草稿（不新建）。

    与 ai_draft 的区别：
        - ai_draft  : 从要点生成全新文书，落在一份新草稿上；
        - ai_rewrite: 读取现有草稿全文，交给 LLM 优化结构、用语、
                      逻辑后覆盖写回，支持用户附加要求（如"语气更正式"）。

    参数:
        doc_id: 草稿 ID（必须是当前用户自己的）
        instruction: 用户的附加重写要求（可为空）
    """
    from src.core.llm_client import llm_client

    row = _own_doc(user, doc_id)                 # 归属校验（数据隔离）
    old_content = (row.get("content") or "").strip()
    if not old_content:
        raise HTTPException(status_code=400,
                            detail="当前文书内容为空，请先编写内容或使用「AI 智能起草」")

    system_prompt = (
        "你是资深执业律师兼法律文书编辑。请对用户提供的法律文书草稿进行"
        "整体重写与润色：优化结构层次、修正法律用语、补全格式规范，"
        "保留原有事实与当事人信息不虚构不遗漏。"
        "如用户提出了附加要求，必须优先满足。"
        "务必使用 Markdown 格式输出，不要输出任何解释，直接输出重写后的完整文书。"
    )
    user_prompt = f"【待重写的文书草稿】\n{old_content}"
    if instruction.strip():
        user_prompt += f"\n\n【附加重写要求】\n{instruction.strip()}"
    user_prompt += f"\n\n【文书类型】{_label_of(row.get('doc_type', ''))}"

    new_content = await _generate_doc_with_retry(
        llm_client.generate, user_prompt, system_prompt)

    update_draft(user, doc_id, content=new_content, status="ai_rewritten")
    log_audit(user["user_id"], user["username"], "DOC_AI_REWRITE", "documents",
              f"AI 重写文书 #{doc_id}", None)

    return {
        "id": doc_id,
        "title": row["title"],
        "doc_type": row["doc_type"],
        "content": new_content,
        "word_count": _count_words(new_content),
    }


# ------------------------------------------------------------------
# LLM 文书生成统一入口：清洗 + 退化乱码检测 + 一次重试
# ------------------------------------------------------------------
_DEGENERATE_MARKS = ("[MASK", "[PAD", "[BOS", "[UNK", "[PLHD",
                     "never_used", "<|")


def _is_degenerate(text: str) -> bool:
    """
    判断 LLM 输出是否为退化乱码。
    特征（1.5b 小模型长文本生成时的典型退化）：
        1. 含特殊 token 泄漏（<[MASK_xxx]> / [PAD] / never_used 等）；
        2. 中文字符占比过低（法律文书应为纯中文为主）。
    """
    if not text:
        return True
    low = text.lower()
    if any(m.lower() in low for m in _DEGENERATE_MARKS):
        return True
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    return chinese / max(len(text), 1) < 0.25


async def _generate_doc_with_retry(generate_fn, user_prompt: str,
                                   system_prompt: str,
                                   max_attempts: int = 2) -> str:
    """
    调用 LLM 生成文书并做质量检测：
        - 输出为空 / 乱码 -> 自动重试（换更明确的指令）；
        - 重试后仍失败 -> 抛 502 友好错误（不保存乱码进库）。
    """
    last_err = ""
    for attempt in range(1, max_attempts + 1):
        prompt = user_prompt
        if attempt > 1:
            prompt = (user_prompt
                      + "\n\n【重要】上一次输出出现乱码或为空，请重新生成，"
                        "只输出规范的中法律文书正文。")
        raw = await generate_fn(
            prompt, system=system_prompt,
            temperature=0.1, max_tokens=config.DOC_MAX_TOKENS)
        content = _clean_llm_doc(raw or "")
        if content and not _is_degenerate(content):
            return content
        last_err = "输出为空" if not content else "输出含乱码"
        logger.warning("AI 文书生成第 %d 次失败（%s），%s",
                       attempt, last_err, "准备重试" if attempt < max_attempts else "放弃")
    raise HTTPException(
        status_code=502,
        detail="AI 生成质量异常（模型输出乱码），请稍后重试；"
               "若多次失败请联系管理员检查本地模型服务。")


def _label_of(doc_type: str) -> str:
    for t in DOC_TYPES:
        if t["value"] == doc_type:
            return t["label"]
    return doc_type


def _clean_llm_doc(raw: str) -> str:
    """清洗 LLM 输出：剥离代码围栏、冗余前后缀。"""
    text = raw.strip()
    # 去除前后 ```markdown / ``` 围栏
    text = re.sub(r"^```(?:markdown)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    # 去除 LLM 偶发的"以下是..."等引导语
    prefix = re.compile(
        r"^\s*(?:好的|下面|以下是|这是|根据您|基于您|我将).*?[：:]\s*\n",
        re.DOTALL)
    text = prefix.sub("", text)
    return text.strip()


def _count_words(text: str) -> int:
    """中文字数粗统计（含英文单词按空格切分）。"""
    if not text:
        return 0
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    english = len(re.findall(r"[a-zA-Z]+", text))
    return chinese + english


def _safe_json(v: str | None) -> dict:
    if not v:
        return {}
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except Exception:
        return {}


# ==================================================================
# 五、多格式导出
# ==================================================================
def export_doc(user: dict, doc_id: int, fmt: str) -> tuple[str, bytes, str]:
    """
    导出文书。返回 (filename, bytes_content, content_type)。

    支持格式：docx / md / txt / html
    """
    row = _own_doc(user, doc_id)
    title = row["title"]
    content = row["content"] or ""

    safe_name = _safe_filename(title)
    if fmt == "docx":
        data = _build_docx(title, content, row)
        return f"{safe_name}.docx", data, ("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    elif fmt == "md":
        md_content = content if content.lstrip().startswith("#") else f"# {title}\n\n{content}"
        return f"{safe_name}.md", md_content.encode("utf-8"), "text/markdown; charset=utf-8"
    elif fmt == "txt":
        plain = _md_to_plain(content)
        return f"{safe_name}.txt", plain.encode("utf-8"), "text/plain; charset=utf-8"
    elif fmt == "html":
        html = _md_to_html(content, title)
        return f"{safe_name}.html", html.encode("utf-8"), "text/html; charset=utf-8"
    else:
        raise HTTPException(status_code=400, detail=f"不支持的导出格式: {fmt}")


def _safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n]', "_", title or "文书")[:60]
    return name


def _md_to_plain(md: str) -> str:
    """极简 Markdown -> 纯文本（去标题符号、粗体、列表标记）。"""
    text = md or ""
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_]{1,3}([^*_\n]+?)[*_]{1,3}", r"\1", text)
    text = re.sub(r"^[-*+]\s*", "· ", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _md_to_html(md: str, title: str = "") -> str:
    """极简 Markdown -> HTML（满足 Word/WPS 打开需求）。"""
    lines = (md or "").split("\n")
    html_parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{title}</title>",
        "<style>body{font-family:'宋体',serif;line-height:1.8;"
        "max-width:800px;margin:40px auto;padding:0 20px;}"
        "h1,h2,h3{color:#1F4E79;}p{text-indent:2em;}</style>",
        "</head><body>",
    ]
    in_list = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append("<br/>")
            continue
        h = re.match(r"^(#{1,6})\s+(.+)", stripped)
        if h:
            level = len(h.group(1))
            html_parts.append(f"<h{level}>{h.group(2)}</h{level}>")
            continue
        if re.match(r"^[-*+]\s+", stripped):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            li_text = re.sub(r"^[-*+]\s+", "", stripped)
            html_parts.append(f"<li>{li_text}</li>")
            continue
        if in_list:
            html_parts.append("</ul>")
            in_list = False
        # 简单处理粗体
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", stripped)
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        html_parts.append(f"<p>{text}</p>")
    if in_list:
        html_parts.append("</ul>")
    html_parts.append("</body></html>")
    return "\n".join(html_parts)


def _build_docx(title: str, content: str, doc_row: dict) -> bytes:
    """
    生成 .docx 文件（python-docx）。
    利用已有 contract.exporter 的中文字体设置经验，
    确保宋体/黑体正确渲染（w:eastAsia + w:ascii 双设置）。
    """
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.oxml.ns import qn
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"python-docx 未安装: {e}")

    doc = Document()

    # --- 统一样式的东亚字体 ---
    def _apply_cjk(style_obj, east_asia: str) -> None:
        style_obj.font.name = "宋体"
        rpr = style_obj.element.get_or_add_rPr()
        rpr.get_or_add_rFonts().set(qn("w:eastAsia"), east_asia)

    _apply_cjk(doc.styles["Normal"], "宋体")
    for h in ("Title", "Heading 1", "Heading 2", "Heading 3"):
        try:
            _apply_cjk(doc.styles[h], "黑体")
        except KeyError:
            pass

    # --- 大标题 ---
    doc_type_label = _label_of(doc_row.get("doc_type", ""))
    heading = doc.add_heading("", level=0)
    run = heading.add_run(doc_type_label)
    run.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)

    # --- 文书标题 ---
    title_p = doc.add_paragraph()
    title_run = title_p.add_run(title)
    title_run.bold = True
    title_run.font.size = Pt(14)
    title_run.font.name = "宋体"
    title_run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")

    # --- 元信息小注 ---
    meta_p = doc.add_paragraph()
    meta_p.add_run(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}    ").font.size = Pt(9)
    meta_p.add_run(f"字数：{doc_row.get('word_count', 0)}").font.size = Pt(9)

    doc.add_paragraph()

    # --- Markdown 正文逐行解析 ---
    for line in (content or "").split("\n"):
        stripped = line.strip()
        if not stripped:
            doc.add_paragraph("")
            continue

        h = re.match(r"^(#{1,6})\s+(.+)", stripped)
        if h:
            level = min(len(h.group(1)), 3)
            hp = doc.add_heading("", level=level)
            rr = hp.add_run(h.group(2))
            rr.font.name = "黑体"
            rr._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "黑体")
            continue

        # 列表项
        if re.match(r"^[-*+]\s+", stripped):
            text = re.sub(r"^[-*+]\s+", "", stripped)
            p = doc.add_paragraph(text, style="List Bullet")
            for run in p.runs:
                run.font.name = "宋体"
                run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
            continue

        # 引用
        if stripped.startswith(">"):
            p = doc.add_paragraph(re.sub(r"^>\s?", "", stripped))
            for run in p.runs:
                run.font.size = Pt(10)
            continue

        # 普通段落：保留 Markdown 粗体
        text = stripped
        p = doc.add_paragraph()
        parts = re.split(r"(\*\*.+?\*\*)", text)
        for part in parts:
            if part.startswith("**") and part.endswith("**"):
                run = p.add_run(part[2:-2])
                run.bold = True
            else:
                p.add_run(part)
        for run in p.runs:
            run.font.name = "宋体"
            run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")

    # --- 页脚小注 ---
    doc.add_paragraph()
    foot = doc.add_paragraph()
    foot_run = foot.add_run("本文档由智法通 · AI 法律顾问系统自动生成，仅供参考。")
    foot_run.italic = True
    foot_run.font.size = Pt(9)
    foot_run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
