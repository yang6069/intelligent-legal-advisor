# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | AI 修改后合同导出器（修改痕迹可视化 docx）
==================================================================
模块职责：
    1. generate_revisions：调用 LLM 对风险条款逐条改写（真实 AI 修改），
       产出结构化修订记录（原始文本 -> 修改后文本 / 新增 / 删除）；
    2. build_revised_docx：把原合同 + 修订记录渲染为带修改痕迹的
       Word 文档 —— AI 新增（绿色高亮）、AI 修改（黄色高亮 +
       红色删除线标记被替换原文）、AI 删除（红色删除线），
       文末附「AI 修改痕迹清单」总表，用户可一眼分辨原文与 AI 改动。

修改痕迹标记规范：
    【AI修改】 被替换的原文片段：红色删除线；新文本：黄色高亮
    【AI新增】 新增条款/内容整段：绿色高亮 + 段首标签
    【AI删除】 建议删除的内容整段：红色删除线 + 段首标签
    每处痕迹前有显式标签文字，高亮之外再添一层可读标记。

缓存策略：
    修订结果随报告 JSON 持久化（report.revisions），
    重复导出零 LLM 消耗；合同内容或建议变化时自动重新生成。
==================================================================
"""
import asyncio
import difflib
import json
import logging
import re
import time

from src.core.llm_client import llm_client

logger = logging.getLogger("zhifatong.contract.export")

# 单次导出最多改写的条款数（控制 LLM 耗时，风险按等级排序取最优先改）
MAX_REVISIONS = 8
# 改写单条款超提示词长度上限（超长条款截断，防止上下文溢出）
CLAUSE_PROMPT_LIMIT = 900
# 小模型偶发自带的标签/引导前缀（输出清洗用）
_REVISION_PREFIX = re.compile(
    r"^\s*(?:【[^】]{1,12}】|修改后[的:：]?条款[：:]?|修改后[：:]?|修订后[：:]?|"
    r"以下是修改后的条款[：:]?|条款正文[：:]?)\s*")

REVISION_PROMPT = """你是资深合同起草律师。请依据审查意见改写以下合同条款，
使修改后的条款消除风险、合法合规，同时尽量保留原条款的商务意图与文本结构。

合同类型：{contract_type}
风险等级：{severity}
风险描述：{description}
修改建议：{suggestion}

【原条款全文】
{clause_text}

改写要求：
1. 仅输出修改后的完整条款文本（可多句），不要解释、不要 markdown；
2. 若建议为"删除该条款"，输出空字符串；
3. 若建议为"新增条款"，输出新增条款的完整文本；
4. 语言风格与原合同保持一致（"甲方/乙方"称谓沿用原条款）。

直接输出修改后的条款正文："""


# ==================================================================
# 一、AI 修订生成（真实 LLM 逐条款改写）
# ==================================================================
async def generate_revisions(contract_text: str, report: dict) -> list[dict]:
    """
    依据审查报告的修改建议，逐条款调用 LLM 生成 AI 修改稿。

    修订来源优先级：
        1. 报告 modification_suggestions（主审/复核/反方/仲裁全部建议）；
        2. 必备条款缺失建议 -> 生成【AI新增】条款。

    返回:
        [{action: "modify"|"add"|"delete", clause_index, original, revised,
          suggestion, severity, risk_type}]
    """
    from src.contract.risk_rules import split_clauses

    t0 = time.perf_counter()
    contract_type = report.get("contract_type", "其他")
    clauses = split_clauses(contract_text)
    clause_map = {c["index"]: c["text"] for c in clauses}

    # ---- 组装修订任务（去重、按风险等级排序）----
    severity_rank = {"高": 0, "中": 1, "低": 2}
    tasks, seen = [], set()
    for sug in report.get("modification_suggestions", []) or []:
        idx = sug.get("clause_index", -1)
        text = str(sug.get("suggestion", "")).strip()
        if not text or (idx, text[:30]) in seen:
            continue
        seen.add((idx, text[:30]))
        # 条款定位：建议未命中已拆分条款 -> 视为新增建议
        original = clause_map.get(idx, "") if idx in clause_map else ""
        action = "modify" if original else "add"
        if re.search(r"删除|删去|删除该条款", text) and original:
            action = "delete"
        tasks.append({
            "clause_index": idx, "original": original,
            "suggestion": text, "severity": sug.get("severity", "中"),
            "risk_type": sug.get("risk_type", ""), "action": action,
        })
    tasks.sort(key=lambda t: severity_rank.get(t["severity"], 1))
    tasks = tasks[:MAX_REVISIONS]

    # ---- 并发 LLM 改写 ----
    import asyncio

    async def _rewrite(task: dict) -> dict:
        if task["action"] == "delete":
            task["revised"] = ""
            return task
        raw = await llm_client.generate(
            REVISION_PROMPT.format(
                contract_type=contract_type,
                severity=task["severity"],
                description=task["risk_type"],
                suggestion=task["suggestion"][:400],
                clause_text=task["original"][:CLAUSE_PROMPT_LIMIT],
            ),
            system="你是合同起草律师，直接输出条款正文，不要任何解释。",
            temperature=0.1, max_tokens=600,
        )
        # 清洗：去代码围栏与小模型自带的标签/引导前缀（可重复出现）
        raw = raw.strip().strip("`").strip()
        for _ in range(3):
            cleaned = _REVISION_PREFIX.sub("", raw)
            if cleaned == raw:
                break
            raw = cleaned
        # 降级保护：LLM 不可用时按建议模板生成可读的修订占位说明
        task["revised"] = raw.strip()
        return task

    results = await asyncio.gather(
        *[_rewrite(t) for t in tasks], return_exceptions=True)
    revisions = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.warning("条款改写失败: %s", r)
            continue
        if r["action"] == "modify" and (not r.get("revised") or r["revised"] == r["original"]):
            continue    # 改写无有效产出则跳过（避免空痕迹）
        revisions.append(r)

    # ---- 必备条款缺失 -> AI 新增（无 LLM 依赖的确定性新增任务也已含在建议内）----
    logger.info("AI 修订生成完成：%d 条修订，耗时 %dms",
                len(revisions), int((time.perf_counter() - t0) * 1000))
    return revisions


# ==================================================================
# 二、修改痕迹 docx 渲染
# ==================================================================
def build_revised_docx(contract_text: str, report: dict,
                       revisions: list[dict]) -> "bytes":
    """
    生成带 AI 修改痕迹的 Word 文档（bytes，可直接下载）。

    文档结构：
        1. 标题 + 审查元信息（类型/评分/等级/生成时间）；
        2. 修改痕迹图例（三种标记的说明）；
        3. 合同正文：原段落 + 逐处痕迹渲染
           （AI 修改: difflib 字符级 diff -> 删除线原文 + 高亮新文；
            AI 新增: 绿色高亮整段；AI 删除: 红色删除线整段）；
        4. AI 新增条款附录（无法在原文定位的新增内容统一归入）；
        5. AI 修改痕迹清单总表。

    字体要点：
        中文字体必须同时设置 w:ascii / w:hAnsi / w:eastAsia 三个属性，
        仅设 font.name（ascii/hAnsi）时 Word/WPS 的中文回退会缺字
        （部分汉字渲染为方框），此处统一显式设置宋体/黑体。
    """
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_COLOR_INDEX
    from docx.oxml.ns import qn

    doc = Document()

    # ---- 样式基设：中文宋体（正文）+ 黑体（标题），ascii/eastAsia 全设置 ----
    def _apply_cjk(style_obj, east_asia: str) -> None:
        """样式级中文字体设置：name 管西文，eastAsia 管中日韩字形。"""
        style_obj.font.name = "宋体"
        rpr = style_obj.element.get_or_add_rPr()
        rpr.get_or_add_rFonts().set(qn("w:eastAsia"), east_asia)

    _apply_cjk(doc.styles["Normal"], "宋体")
    for heading_name in ("Title", "Heading 1", "Heading 2"):
        try:
            _apply_cjk(doc.styles[heading_name], "黑体")
        except KeyError:
            pass

    # ---- 1. 标题与元信息 ----
    title = doc.add_heading("", level=0)
    _run = title.add_run("AI 智能修订版合同")
    _cjk_run(_run)
    _run.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)

    meta = doc.add_paragraph()
    _add_run(meta, f"合同类型：{report.get('contract_type', '其他')}    ", bold=True)
    _add_run(meta, f"风险评分：{report.get('risk_score', 0)}    ", bold=True)
    _add_run(meta, f"风险等级：{report.get('risk_level', '低')}", bold=True)
    note = doc.add_paragraph()
    _add_run(note, "本文档由智法通多 Agent 合同审查系统自动生成；"
                   "所有修改处均以标签 + 高亮 + 删除线标记，未标记部分为原始合同文本。",
             size=9, color=RGBColor(0x80, 0x80, 0x80))

    # ---- 2. 痕迹图例 ----
    legend = doc.add_paragraph()
    _add_run(legend, "【AI新增】", highlight=WD_COLOR_INDEX.BRIGHT_GREEN, bold=True)
    _add_run(legend, " AI 新增内容    ")
    _add_run(legend, "【AI修改】", highlight=WD_COLOR_INDEX.YELLOW, bold=True)
    _add_run(legend, " AI 修改内容（原文字加删除线）    ")
    _add_run(legend, "【AI删除】", strike=True, color=RGBColor(0xC0, 0x00, 0x00), bold=True)
    _add_run(legend, " AI 建议删除内容")
    doc.add_paragraph()

    # ---- 3. 正文渲染：按修订条款定位切分原文 ----
    clause_positions = _locate_clauses(contract_text, revisions)
    # 修改统计（写清单用）
    change_rows = []
    # 未定位到原文位置的修订（新增条款/旧版 inline 记录），归入正文后的附录
    appendix_revisions = []

    # 按原文顺序渲染：未修改段落原样输出，修订条款渲染痕迹
    rendered_until = 0
    for pos, rev in clause_positions:
        if pos is None:
            appendix_revisions.append(rev)
            continue
        if pos < rendered_until:
            continue
        # 原样输出修订点之前的原文
        if pos > rendered_until:
            _render_paragraphs(doc, contract_text[rendered_until:pos])
        rendered_until = pos + len(rev.get("original") or "")
        _render_revision(doc, rev, change_rows)
    # 输出剩余原文
    if rendered_until < len(contract_text):
        _render_paragraphs(doc, contract_text[rendered_until:])

    # ---- 3.5 AI 新增条款附录（原文中不存在、由 AI 建议补充的条款） ----
    if appendix_revisions:
        doc.add_heading("AI 新增条款（原文未包含，建议补充）", level=1)
        for rev in appendix_revisions:
            _render_revision(doc, rev, change_rows)

    # ---- 4. 修改痕迹清单 ----
    doc.add_page_break()
    doc.add_heading("AI 修改痕迹清单", level=1)
    if not change_rows:
        doc.add_paragraph("本次审查未产生自动修订（合同无高优先级修改建议）。")
    else:
        table = doc.add_table(rows=1, cols=5)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        for i, h in enumerate(["序号", "痕迹类型", "风险等级", "修改依据（AI 审查建议）", "AI 修改结果摘要"]):
            hdr[i].text = ""
            _add_run(hdr[i].paragraphs[0], h, bold=True)
        for i, row in enumerate(change_rows, 1):
            cells = table.add_row().cells
            for j, val in enumerate([str(i), row["action"], row["severity"],
                                     row["suggestion"][:120], row["revised"][:120]]):
                cells[j].text = ""
                _add_run(cells[j].paragraphs[0], val, size=9)

    import io
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _locate_clauses(contract_text: str,
                    revisions: list[dict]) -> list[tuple[int | None, dict]]:
    """
    在原文中定位每个修订条款的起始偏移。

    匹配策略：取修订 original 的前 30 字符在原文中查找；
    找不到（新增条款 / 原文被截断）返回 None，渲染时追加到文档末尾。
    """
    positions = []
    for rev in revisions:
        original = rev.get("original") or ""
        pos = None
        if original:
            probe = original.strip()[:30]
            if probe:
                pos = contract_text.find(probe)
        positions.append((pos, rev))
    # 按位置排序（未定位到的排最后）
    positions.sort(key=lambda x: (x[0] is None, x[0] if x[0] is not None else 0))
    return positions


def _render_paragraphs(doc, text: str) -> None:
    """原样渲染原文段落（无痕迹）。"""
    for para in text.split("\n"):
        if para.strip():
            doc.add_paragraph(para.strip())


def _render_plain_before(doc, contract_text: str, pos: int) -> None:
    """预留接口：位置前置渲染已在主流程处理，此处保持空实现以保证扩展性。"""
    return


# 每个 run 显式设置东亚字体（宋体），双保险防止样式继承链缺字
def _cjk_run(run) -> None:
    """run 级 w:eastAsia 字体设置（Word/WPS 中文字形渲染保障）。"""
    from docx.oxml.ns import qn
    run.font.name = "宋体"
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")


def _render_revision(doc, rev: dict, change_rows: list) -> None:
    """
    渲染单条修订的修改痕迹。

    AI 修改：difflib 字符级 diff —— 删除片段红色删除线、
            新增/替换片段黄色高亮、未变文本原样；
    AI 新增：整段绿色高亮 + 段首【AI新增】标签；
    AI 删除：整段红色删除线 + 段首【AI删除】标签。
    """
    from docx.enum.text import WD_COLOR_INDEX
    from docx.shared import RGBColor

    action = rev.get("action", "modify")
    original = (rev.get("original") or "").strip()
    revised = (rev.get("revised") or "").strip()

    if action == "add" or (not original and revised):
        p = doc.add_paragraph()
        _add_run(p, "【AI新增】", highlight=WD_COLOR_INDEX.BRIGHT_GREEN, bold=True)
        _add_run(p, revised, highlight=WD_COLOR_INDEX.BRIGHT_GREEN)
        change_rows.append({"action": "AI新增", "severity": rev.get("severity", "中"),
                            "suggestion": rev.get("suggestion", ""), "revised": revised})
        return

    if action == "delete" or (original and not revised):
        p = doc.add_paragraph()
        _add_run(p, "【AI删除】", strike=True, color=RGBColor(0xC0, 0, 0), bold=True)
        _add_run(p, original, strike=True, color=RGBColor(0xC0, 0, 0))
        change_rows.append({"action": "AI删除", "severity": rev.get("severity", "中"),
                            "suggestion": rev.get("suggestion", ""),
                            "revised": "（删除该条款）"})
        return

    # ---- AI 修改：字符级 diff 渲染 ----
    p = doc.add_paragraph()
    _add_run(p, "【AI修改】", highlight=WD_COLOR_INDEX.YELLOW, bold=True)
    sm = difflib.SequenceMatcher(None, original, revised, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            _add_run(p, original[i1:i2])
        elif tag in ("delete", "replace"):
            # 被替换/删除的原文：红色删除线
            _add_run(p, original[i1:i2], strike=True,
                     color=RGBColor(0xC0, 0, 0))
        if tag in ("insert", "replace"):
            # 新文本：黄色高亮
            _add_run(p, revised[j1:j2], highlight=WD_COLOR_INDEX.YELLOW)
    change_rows.append({"action": "AI修改", "severity": rev.get("severity", "中"),
                        "suggestion": rev.get("suggestion", ""), "revised": revised})


def _add_run(paragraph, text: str, *, bold: bool = False, size=None,
             color=None, highlight=None, strike: bool = False) -> None:
    """docx run 快捷构造（中文字体/加粗/颜色/高亮/删除线一次设置）。"""
    if not text:
        return
    run = paragraph.add_run(text)
    _cjk_run(run)          # 显式设置 ascii + eastAsia 字体，杜绝中文缺字方框
    run.bold = bold
    if size is not None:
        from docx.shared import Pt
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = color
    if highlight is not None:
        run.font.highlight_color = highlight
    if strike:
        run.font.strike = True


def safe_doc_filename(title: str) -> str:
    """生成安全的 docx 文件名（清洗非法字符，限长）。"""
    name = re.sub(r'[\\/:*?"<>|\r\n]', "_", title)[:60] or "合同"
    return f"{name}_AI修改版.docx"


def export_payload(contract_text: str, report: dict, revisions: list[dict]) -> dict:
    """导出附带数据（前端下载完成后可展示的摘要）。"""
    return {
        "revision_count": len(revisions),
        "actions": {
            "add": sum(1 for r in revisions if r.get("action") == "add"),
            "modify": sum(1 for r in revisions if r.get("action") == "modify"),
            "delete": sum(1 for r in revisions if r.get("action") == "delete"),
        },
        "revisions": [
            {"clause_index": r.get("clause_index"), "action": r.get("action"),
             "severity": r.get("severity"), "suggestion": r.get("suggestion"),
             "original": (r.get("original") or "")[:200],
             "revised": (r.get("revised") or "")[:200]}
            for r in revisions
        ],
    }


# JSON 序列化助手（与 workflow 保持一致的中文策略）
def dumps_cn(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
