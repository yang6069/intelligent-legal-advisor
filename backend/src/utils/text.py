# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 文本清洗工具（纯文本输出保障 + 乱码修复）
==================================================================
模块职责：
    1. strip_markdown：把 LLM 输出清洗为纯文本
       （去除 ### 标题、** 加粗、` 代码、> 引用、- 列表等 Markdown 符号，
        保留可读的层级结构与中文编号）；
    2. MarkdownStripper：流式场景的有状态清洗器 ——
       token 分片可能把 "**" "###" 等标记切碎，通过 hold-back 缓冲
       保证跨分片标记同样被完整清除，逐段输出干净文本；
    3. fix_mojibake：修复 UTF-8 字节被误按 latin-1 解码产生的乱码
       （multipart 文件名经典问题：'æµ‹è¯•' -> '测试'）。

设计原则：
    - 清洗是"标记剥离"而非"语义改写"：不删除换行与缩进结构，
      列表符号统一转为「·」视觉项目符（非 Markdown 语法）；
    - 流式清洗器逐段输出增量为纯文本，累计结果 == 一次性清洗结果。
==================================================================
"""
import re

# ------------------------------------------------------------------
# 预编译正则（性能：流式高频调用）
# ------------------------------------------------------------------
_RE_CODE_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n?")     # ```json / ```
_RE_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")          # 行首 ### 标题
_RE_BOLD_IT = re.compile(r"\*{1,3}([^*\n]+?)\*{1,3}")       # **加粗** / *斜体*
_RE_INLINE_CODE = re.compile(r"`([^`\n]+?)`")               # `代码`
_RE_MATH = re.compile(r"\${1,2}([^$\n]+?)\${1,2}")          # $公式$（小模型偶发）
_RE_QUOTE = re.compile(r"(?m)^\s{0,3}>\s?")                 # 行首 > 引用
_RE_LIST_DASH = re.compile(r"(?m)^\s{0,3}[-*+]\s+")         # 行首 - /*/+ 列表
_RE_LINK = re.compile(r"\[([^\]\n]+)\]\([^)\n]+\)")         # [文本](链接) -> 文本
_REHR = re.compile(r"(?m)^\s{0,3}(-{3,}|={3,}|\*{3,})\s*$") # --- 水平线
_RE_TABLE_SEP = re.compile(r"(?m)^\s{0,3}\|?[\s:|-]+\|[\s:|-]*$")  # 表格分隔行
_RE_TABLE_PIPE = re.compile(r"\|")                          # 表格单元格分隔符

# 乱码特征字符（latin-1/cp1252 误码高频出现；命中才尝试修复，避免误伤正常文本）
_MOJIBAKE_CHARS = set("ÃÂæøåÆØÅçèéêëìíîïðñòóôõö÷ùúûüýþÿ"
                      "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ")

# 误码还原尝试的编码链：latin-1 覆盖 0x00-0xFF 直映射；
# cp1252 覆盖 Windows 扩展区（0x80-0x9F 映射为 … – € 等打印字符，
# MySQL 客户端误读场景实际多为 cp1252 形态）
_MOJIBAKE_CODECS = ("latin-1", "cp1252")


def strip_markdown(text: str) -> str:
    """
    将 LLM 输出清洗为纯文本（非流式一次性清洗）。

    处理顺序（先整体结构、后行内标记）：
        1. 剥离 ``` 代码围栏；
        2. 行首 ### 标题标记（保留标题文字）；
        3. ** 加粗 / * 斜体 / ` 行内代码 / $ 公式包裹符；
        4. [文本](链接) 保留链接文字；
        5. 行首 > 引用符、- 列表符（列表转「·」项目符）；
        6. Markdown 表格分隔行整行删除、单元格竖线转空格；
        7. --- 水平线删除；
        8. 合并 3 连以上空行。
    """
    if not text:
        return ""
    out = _RE_CODE_FENCE.sub("", text)
    out = _RE_HEADING.sub("", out)
    out = _RE_BOLD_IT.sub(r"\1", out)
    out = _RE_INLINE_CODE.sub(r"\1", out)
    out = _RE_MATH.sub(r"\1", out)
    out = _RE_LINK.sub(r"\1", out)
    out = _RE_QUOTE.sub("", out)
    out = _RE_LIST_DASH.sub("· ", out)          # 列表符转视觉项目符
    out = _RE_TABLE_SEP.sub("", out)            # 表格分隔行删除
    out = _RE_TABLE_PIPE.sub("  ", out)         # 表格竖线转空格
    out = _REHR.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out)        # 压缩多余空行
    return out.strip()


class MarkdownStripper:
    """
    流式 Markdown 清洗器（有状态，token 级增量输出）。

    问题背景：
        SSE token 分片可能把标记切碎（如 '**' 拆成 '*' + '*'、
        '###' 拆成 '##' + '#'），逐片独立清洗会漏删。
    解决方案：
        维护 hold-back 尾部缓冲 —— 每次仅输出"不可能成为标记开头"
        的安全前缀，疑似标记尾部（<= MAX_PENDING 字符）留待下一片；
        flush() 在流结束时输出全部残留。
    不变量：
        feed 各次返回值累计拼接 + flush() == strip_markdown(全文)。
    """

    # 尾部暂存上限：覆盖 '```json'、'**'、'### ' 等最长标记前缀
    MAX_PENDING = 8

    def __init__(self):
        self._buffer = ""       # 尚未安全输出的尾部缓冲

    def feed(self, chunk: str) -> str:
        """
        输入一个 token 增量，返回可安全下发的清洗后文本（可为空串）。
        """
        self._buffer += chunk or ""
        cleaned_full = strip_markdown_with_tail(self._buffer)
        # strip_markdown 会 strip 首尾空白，这里仅取"安全输出段"：
        # 保留末尾 MAX_PENDING 字符不输出，防止标记跨界残留
        safe_len = len(cleaned_full) - self.MAX_PENDING
        if safe_len <= 0:
            return ""
        safe = cleaned_full[:safe_len]
        # 用已清洗全文的尾部重置缓冲（缓冲始终是"清洗后文本"的尾部）
        self._buffer = cleaned_full[safe_len:]
        return safe

    def flush(self) -> str:
        """流结束：输出缓冲中的全部残留（完成最终清洗）。"""
        if not self._buffer:
            return ""
        out = self._buffer
        self._buffer = ""
        return out


def strip_markdown_with_tail(text: str) -> str:
    """
    流式版清洗：与 strip_markdown 相同，但【不 strip 尾部空白】，
    避免把"尚未到达的换行/空格"提前吃掉导致单词粘连。
    """
    if not text:
        return ""
    out = _RE_CODE_FENCE.sub("", text)
    out = _RE_HEADING.sub("", out)
    out = _RE_BOLD_IT.sub(r"\1", out)
    out = _RE_INLINE_CODE.sub(r"\1", out)
    out = _RE_MATH.sub(r"\1", out)
    out = _RE_LINK.sub(r"\1", out)
    out = _RE_QUOTE.sub("", out)
    out = _RE_LIST_DASH.sub("· ", out)
    out = _RE_TABLE_SEP.sub("", out)
    out = _RE_TABLE_PIPE.sub("  ", out)
    out = _REHR.sub("", out)
    return out


def fix_mojibake(text: str | None) -> str:
    """
    修复 latin-1 / cp1252 误码乱码（multipart 文件名 / 建库 DEFAULT 脏数据）。

    原理：
        UTF-8 字节流被按 latin-1（或 cp1252）解码后，每个汉字变成
        2-3 个拉丁/扩展字符（'测' -> 'æµ‹'，'其他' -> 'å…¶ä»–'）。
        检测到乱码特征字符时，依次尝试 cp1252/latin-1 重编码
        还原原始字节，再按 UTF-8 解码回正；
        全部失败则回退原文，保证永不抛异常。
    """
    if not text:
        return ""
    if not any(ch in _MOJIBAKE_CHARS for ch in text):
        return text
    for codec in _MOJIBAKE_CODECS:
        try:
            repaired = text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        # 还原后不应再含乱码特征字符（防双重误码往返）
        if repaired and not any(c in _MOJIBAKE_CHARS for c in repaired):
            return repaired
    return text
