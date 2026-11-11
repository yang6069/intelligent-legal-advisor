# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 法律文档分块器（语义感知递归分块）
==================================================================
模块职责：
    将法律文档切分为适合向量检索的语义块：
    1. 法条类文档（law）：以条款为单位分块（一条一款一自然语义单元）；
    2. 案例类文档（case）：按"案情/裁判要旨/结果"段落分块；
    3. 通用文档：递归字符分块（分隔符优先级：\\n\\n > \\n > 。 > ；），
       超长块二次切分，相邻块保留 overlap 重叠保证上下文连贯。

设计依据（需求文档 4.1.3）：
    chunk_size=512 字符 / overlap=64 字符 / legal_aware=True
==================================================================
"""
import re

from src import config

# 条款编号匹配：第X条 / 第X条第X款 / 第一百零三条（中文数字）
_ARTICLE_PATTERN = re.compile(
    r"(第[一二三四五六七八九十百千零\d]+条(?:之[一二三四五六七八九十\d]+)?)"
)


def chunk_document(doc: dict) -> list[dict]:
    """
    对单个法律文档分块。

    参数:
        doc: {"id": 文档ID, "title": 标题, "doc_type": law/case/..., "content": 全文, ...}

    返回:
        块列表，每块:
        {
          "text": 块文本（含标题头部，增强检索语义）,
          "doc_id": 文档ID,
          "doc_type": ..., "title": ..., "law_name": ..., "article_no": ...,
          "chunk_index": 块序号, "effectiveness": ...
        }
    """
    content = (doc.get("content") or "").strip()
    if not content:
        return []

    # 按文档类型选择分块策略
    if doc.get("doc_type") == "law":
        pieces = _split_by_article(content)
    elif doc.get("doc_type") == "case":
        pieces = _split_by_paragraph(content)
    else:
        pieces = _split_by_paragraph(content)

    # 块头部：拼接文档标题与法条号，提升向量检索的语义定位能力
    chunks = []
    for i, piece in enumerate(pieces):
        article_no = _extract_article_no(piece) or doc.get("article_no", "")
        header = f"【{doc.get('title', '')}】{'（' + article_no + '）' if article_no else ''}\n"
        text = (header + piece).strip()
        if len(text) < 20:      # 过滤过短碎片
            continue
        chunks.append({
            "text": text,
            "doc_id": doc.get("id"),
            "doc_type": doc.get("doc_type"),
            "title": doc.get("title"),
            "law_name": doc.get("law_name", ""),
            "article_no": article_no,
            "effectiveness": doc.get("effectiveness", "有效"),
            "chunk_index": i,
        })
    return chunks


# ==================================================================
# 内部策略实现
# ==================================================================
def _split_by_article(content: str) -> list[str]:
    """
    法条级分块：以"第X条"为切分锚点。

    每个条文独立成块；相邻条文合并条件：
        单条过短（<120 字）时与下一条合并，避免碎片化。
    """
    # 在"第X条"前插入换行锚点
    marked = _ARTICLE_PATTERN.sub(lambda m: "\n" + m.group(1), content)
    raw_pieces = [p.strip() for p in marked.split("\n") if p.strip()]

    pieces, buffer = [], ""
    for p in raw_pieces:
        candidate = (buffer + "\n" + p).strip() if buffer else p
        if len(candidate) >= 120:
            pieces.append(candidate)
            buffer = ""
        elif len(candidate) > config.CHUNK_SIZE * 2:
            # 合并后过长：先封存 buffer，长条文单独递归切分
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.extend(_recursive_split(p, config.CHUNK_SIZE, config.CHUNK_OVERLAP))
        else:
            buffer = candidate
    if buffer:
        pieces.append(buffer)
    return pieces


def _split_by_paragraph(content: str) -> list[str]:
    """
    段落级分块：按空行/单换行聚合，超长段落递归切分。
    """
    paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
    pieces, buffer = [], ""
    for p in paragraphs:
        candidate = (buffer + "\n" + p).strip() if buffer else p
        if len(candidate) > config.CHUNK_SIZE:
            if buffer:
                pieces.append(buffer)
            # 单段即超限：递归切分
            pieces.extend(_recursive_split(p, config.CHUNK_SIZE, config.CHUNK_OVERLAP))
            buffer = ""
        else:
            buffer = candidate
    if buffer:
        pieces.append(buffer)
    return pieces


def _recursive_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    递归字符分块（降级策略）。

    分隔符优先级: "。" > "；" > "，" > 硬切。
    相邻块保留 overlap 字符重叠，保证上下文连贯性。
    """
    if len(text) <= chunk_size:
        return [text]
    # 尝试按句子切
    sentences = re.split(r"(?<=[。；])", text)
    sentences = [s for s in sentences if s.strip()]
    pieces, buffer = [], ""
    for s in sentences:
        candidate = buffer + s
        if len(candidate) > chunk_size:
            if buffer:
                pieces.append(buffer)
            # 单句超长：硬切
            if len(s) > chunk_size * 2:
                for i in range(0, len(s), chunk_size - overlap):
                    pieces.append(s[i:i + chunk_size])
                buffer = ""
            else:
                buffer = s
        else:
            buffer = candidate
    if buffer:
        pieces.append(buffer)
    # 叠加重叠：块间共享前块尾部 overlap 字符
    if overlap > 0 and len(pieces) > 1:
        overlapped = [pieces[0]]
        for prev, cur in zip(pieces, pieces[1:]):
            tail = prev[-overlap:]
            overlapped.append(tail + cur if not cur.startswith(tail) else cur)
        return overlapped
    return pieces


def _extract_article_no(text: str) -> str:
    """提取块内出现的第一个条款编号（如"第497条"）。"""
    m = _ARTICLE_PATTERN.search(text[:80])
    return m.group(1) if m else ""
