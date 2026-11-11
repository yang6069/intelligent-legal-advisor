# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 合同文档解析器
==================================================================
模块职责：
    1. 多格式合同文本提取（.docx / .pdf / .txt / .md）；
    2. 合同类型自动识别（买卖/租赁/劳动/服务/保密/借款/其他）；
    3. 当事方信息提取（甲方/乙方名称与角色）；
    4. 路径安全校验（防路径遍历攻击，V5.0 安全加固）。

设计要点：
    - docx 用 python-docx（段落+表格文本）；
    - pdf 用 pdfplumber（逐页提取）；
    - 兜底按纯文本读取（UTF-8/GBK 双编码尝试）。
==================================================================
"""
import logging
import re
from pathlib import Path

from src import config

logger = logging.getLogger("zhifatong.contract.parser")

# 支持的上传扩展名（白名单制）
ALLOWED_EXTENSIONS = {".docx", ".doc", ".pdf", ".txt", ".md"}
MAX_FILE_SIZE = 10 * 1024 * 1024     # 10MB 上限

# 合同类型识别关键词表（按匹配优先级排序——越专用越靠前）
_CONTRACT_TYPE_KEYWORDS = [
    ("劳动合同", ["劳动合同", "劳动关系", "试用期", "用人单位", "劳动者", "竞业限制"]),
    ("教育培训合同", ["培训服务", "培训机构", "课时", "学费", "学员", "课程", "教学", "教师资格"]),
    ("租赁合同", ["租赁合同", "房租", "出租人", "承租人", "房东", "租客"]),
    ("买卖合同", ["买卖合同", "购销", "供货", "采购合同", "卖方", "买方", "标的物"]),
    ("借款合同", ["借款合同", "出借人", "借款人", "贷款", "出借", "债务"]),
    ("服务合同", ["服务合同", "服务内容", "服务费用", "外包", "咨询服务", "服务期"]),
    ("保密协议", ["保密协议", "保密义务", "商业秘密", "保密期限", "竞业"]),
    ("技术合同", ["技术开发", "技术转让", "知识产权", "技术秘密", "源代码"]),
]


def validate_file(filename: str, size: int) -> None:
    """
    上传文件安全校验。

    校验项: 扩展名白名单 + 大小上限 + 文件名路径穿越检测。
    异常: ValueError（含具体原因）
    """
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式 {ext}，仅支持 docx/pdf/txt/md")
    if size > MAX_FILE_SIZE:
        raise ValueError(f"文件超过 {MAX_FILE_SIZE // 1024 // 1024}MB 上限")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise ValueError("非法文件名")


def extract_text(file_path: str) -> str:
    """
    提取合同全文文本（按扩展名分派解析器）。

    返回:
        纯文本（前后空白已清理）；提取失败抛 ValueError。
    """
    path = Path(file_path)
    if not path.exists():
        raise ValueError(f"文件不存在: {file_path}")
    ext = path.suffix.lower()

    if ext == ".docx":
        return _extract_docx(path)
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext in (".txt", ".md", ".doc"):
        return _extract_plain(path)
    raise ValueError(f"不支持的格式: {ext}")


def _extract_docx(path: Path) -> str:
    """Word 文档文本提取（段落 + 表格单元格）。"""
    import docx
    doc = docx.Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts).strip()


def _extract_pdf(path: Path) -> str:
    """PDF 文本提取（逐页聚合）。"""
    import pdfplumber
    parts = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text.strip())
    return "\n".join(parts).strip()


def _extract_plain(path: Path) -> str:
    """纯文本提取（UTF-8 优先，GBK 兜底）。"""
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            return path.read_text(encoding=encoding).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError("文件编码无法识别（仅支持 UTF-8/GBK）")


# ==================================================================
# 合同结构化信息识别
# ==================================================================
def detect_contract_type(text: str) -> str:
    """
    合同类型自动识别。

    算法: 关键词计分制——统计各类型关键词命中数，取最高分；
          无命中返回"其他"。
    """
    best_type, best_score = "其他", 0
    for ctype, keywords in _CONTRACT_TYPE_KEYWORDS:
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_type, best_score = ctype, score
    return best_type


def extract_parties(text: str) -> list[dict]:
    """
    提取合同当事方信息。

    模式:
        "甲方：XXX" / "乙方（承租方）：XXX" / "用人单位：XXX" 等常见格式。

    返回:
        [{"role": "甲方", "name": "XX科技有限公司"}, ...]
    """
    parties = []
    # 匹配 "XX方：名称" 或 "称谓：名称"（限前 20 行，当事方通常在合同头部）
    head = "\n".join(text.splitlines()[:30])
    pattern = re.compile(
        r"(甲方|乙方|丙方|丁方|买方|卖方|出租方|承租方|发包方|承包方|用人单位|劳动者|"
        r"出租人|承租人|借款人|贷款人|雇主|雇员|聘请方|受聘方|委托方|受托方)"
        r"[（(]?[^：:\n]{0,8}[）)]?\s*[：:]\s*([^\n，。;；]{2,40})"
    )
    for m in pattern.finditer(head):
        role, name = m.group(1), m.group(2).strip()
        if name and not any(p["role"] == role for p in parties):
            parties.append({"role": role, "name": name})
    if not parties:
        # 兜底：提取前两行作为当事方线索
        parties = [{"role": "甲方", "name": "未识别"}, {"role": "乙方", "name": "未识别"}]
    return parties[:6]


def build_safe_path(user_id: int, contract_id: int, filename: str) -> Path:
    """
    生成安全的存储路径（防路径遍历）。

    路径规则: uploads/{user_id}/{contract_id}_{清洗后文件名}
    """
    safe_name = re.sub(r"[^\w\u4e00-\u9fa5.-]", "_", filename)[:80]
    directory = config.UPLOAD_DIR / str(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{contract_id}_{safe_name}"
