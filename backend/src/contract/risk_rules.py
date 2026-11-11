# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 合同条款拆分器 + 风险规则引擎
==================================================================
模块职责：
    1. 条款拆分（clause_splitter）：按"第X条"锚点拆分合同条款，
       识别每条的风险关键词热区，供三方 Agent 审查；
    2. 风险规则引擎（RiskRuleEngine）：16 条内建规则覆盖高频合同风险
       （单方权利、违约金畸高、定金超限、管辖不利、自动续期等），
       规则命中产出确定性的风险项（与 LLM 审查互补，可交叉验证）。

设计依据（需求文档 6.2.1）:
    规则引擎结论作为"复核 Agent"的判定依据，保证审查结果
    的可解释性与确定性（LLM 仅做补充语义分析）。
==================================================================
"""
import re
from dataclasses import dataclass, asdict

# ------------------------------------------------------------------
# 条款编号模式（支持 第X条 / 第X条X款 / 一、二、 1. 2. 等混合编号）
# ------------------------------------------------------------------
_CLAUSE_PATTERN = re.compile(
    r"(?:(第[一二三四五六七八九十百零\d]+条)|(^[一二三四五六七八九十]+[、.．])|(^\d{1,3}[、.．]))",
    re.MULTILINE,
)


def split_clauses(contract_text: str) -> list[dict]:
    """
    合同条款拆分。

    参数:
        contract_text: 合同全文

    返回:
        [{"index": 0, "text": 条款文本, "head": 条款编号头部}]
        无条款结构时按空行聚合为"整段条款"。

    说明:
        - 编号锚点切分后，条款归属前一锚点（标准合同结构）；
        - 合同头部（当事人/鉴于部分）若存在非条款文本，作为 index=-1 前言，
          不参与风险审查但保留在全文中。
    """
    text = contract_text.strip()
    if not text:
        return []

    # 找到所有条款锚点位置
    anchors = [(m.start(), m.group(0)) for m in _CLAUSE_PATTERN.finditer(text)]
    if not anchors:
        # 无编号结构：按双换行聚合
        blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
        return [{"index": i, "text": b, "head": f"段{i + 1}"}
                for i, b in enumerate(blocks)]

    clauses = []
    # 条款 1：从首个锚点到第二锚点
    for i, (pos, head) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        clause_text = text[pos:end].strip()
        if len(clause_text) < 8:            # 过滤噪声碎片
            continue
        clauses.append({
            "index": len(clauses),
            "text": clause_text[:2000],     # 单条截断保护
            "head": head.strip(),
        })

    # 合同前言（首个锚点前的文本，通常含当事方信息）
    preface = text[:anchors[0][0]].strip()
    if len(preface) > 30:
        clauses.insert(0, {
            "index": 0, "text": preface[:2000], "head": "前言（当事方信息）",
        })
        # 重排 index
        for i, c in enumerate(clauses):
            c["index"] = i
    return clauses


# ==================================================================
# 风险规则引擎
# ==================================================================
@dataclass
class RuleHit:
    """规则命中结果。"""
    rule_id: str            # 规则编号（R01...）
    rule_name: str          # 规则名称
    severity: str           # 风险等级：高/中/低
    description: str        # 风险描述
    legal_basis: str        # 法律依据
    suggestion: str         # 修改建议
    matched_text: str       # 命中片段


# 规则定义表：(规则ID, 名称, 等级, 正则模式, 描述, 法律依据, 修改建议)
_RULES: list[tuple] = [
    ("R01", "单方解除权", "高",
     r"(单方解除|单方面终止|有权解除本合同[^，。]*无需[^，。]*同意|任意解除)",
     "合同赋予一方单方解除/终止权利，可能造成权利义务不对等",
     "《民法典》第562条、第563条（合同解除条件法定与约定）",
     "删除单方解除条款，或将解除条件改为双方协商一致/法定情形"),

    ("R02", "单方变更/调价权", "高",
     r"(单方(调整|变更|修改)|有权(调整|变更)[^，。]*(价格|费用|条款)|价格调整[^，。]*无需(通知|同意))",
     "一方可单方调整价格或变更条款，弱势方缺乏议价保障",
     "《民法典》第496条、第497条（格式条款无效情形）",
     "改为“价格调整需双方书面确认”或设置调整上限与通知期"),

    ("R03", "违约金畸高风险", "中",
     r"(违约金|违约责任)[^。]*?(\d{2,3}\s*%|百分之[二三四五六七八九十]{1,3})",
     "违约金比例疑似超过实际损失30%，法院可能调减或认定过高",
     "《民法典》第585条（违约金过高酌减规则）；司法解释：超过损失30%为过高",
     "将违约金上限调整为合同总额20%以内或与实际损失挂钩"),

    ("R04", "定金超标", "中",
     r"定金[^。]*?(\d{2,3}\s*%|百分之[二三四五六七八九十]{1,3})",
     "定金超过主合同标的额20%，超出部分不产生定金效力",
     "《民法典》第586条第2款（定金不得超过主合同标的额20%）",
     "将定金降至标的额20%以内，超出部分改为预付款"),

    ("R05", "免除己方责任", "高",
     r"(?!.*?(不得|不能|禁止|严禁|不得随意))(免除[^，。]*(责任|赔偿)|不承担(任何)?(责任|赔偿|损失)|概不负责|后果自负)",
     "免除/减轻己方责任的格式条款，若加重对方责任则无效",
     "《民法典》第497条第2项（免除己方责任、加重对方责任的格式条款无效）",
     "删除免责条款，或改为“按过错比例分担责任”"),

    ("R06", "自动续期条款", "中",
     r"(自动(续期|续约|延长)|到期(后)?(自动|视为)续|未(提出|书面)异议[^。]*(续|延长))",
     "合同到期自动续期且未设异议期，易形成隐性长期绑定",
     "《民法典》第490条、第733条（合同期限合意原则）",
     "设置“续约需提前30日书面确认”，删除“未异议视为续约”"),

    ("R07", "不利管辖约定", "中",
     r"(管辖|起诉|仲裁)[^。]*?(由[^，。]*(所在地|住所地)[^，。]*(法院|仲裁)|须[^，。]*仲裁)",
     "争议解决条款限定对方须赴特定地诉讼/仲裁，增加维权成本",
     "《民事诉讼法》第35条（协议管辖限制）；《仲裁法》第16条",
     "改为“被告住所地或合同履行地法院管辖”或约定中立仲裁机构"),

    ("R08", "竞业限制无补偿", "高",
     r"(竞业(限制|禁止)|离职后[^。]*(不得|禁止)[^。]*(从业|入职|经营))",
     "竞业限制条款未约定经济补偿，可能被认定无效或需补付",
     "《劳动合同法》第23条、第24条（竞业限制须按月支付补偿）",
     "补充“按月支付不低于离职前12个月平均工资30%的补偿”"),

    ("R09", "试用期超标", "高",
     r"试用期[^。]*?(\d{1,2}\s*(个)?月|一[年]?)",
     "试用期可能超过法定上限（合同期3个月-1年≤1月；1-3年≤2月；3年以上≤6月）",
     "《劳动合同法》第19条（试用期上限）",
     "按合同期限对照法定上限调整试用期长度"),

    ("R10", "模糊表述", "低",
     r"(合理|适当|必要时|相关费用|视情况|另行(通知|协商)|及时)",
     "条款存在模糊表述，履约标准不清易生争议",
     "《民法典》第510条、第511条（约定不明的补充规则）",
     "量化标准：明确金额上限、时间窗口、具体范围"),

    ("R11", "无限责任", "高",
     r"(承担[^，。]*(一切|全部|所有)[^，。]*(损失|责任|费用)|赔偿[^，。]*(一切|全部|无限))",
     "概括性要求承担全部损失/费用，责任范围无限放大",
     "《民法典》第584条（赔偿以可得预见损失为限）",
     "限定赔偿范围：“以直接实际损失为限，且不超过合同总额X%”"),

    ("R12", "放弃诉权", "高",
     r"(放弃(诉讼|起诉|仲裁|申诉)[^，。]*权利|不得(提起诉讼|申请仲裁|投诉))",
     "排除对方诉讼/仲裁权利的条款无效",
     "《民法典》第497条（排除对方主要权利的格式条款无效）",
     "删除该条款，保留法定争议解决权利"),

    ("R13", "所有权保留异常", "中",
     r"(所有权[^。]*(保留|归[^。]*所有)[^。]*(付清|付完|交付))",
     "所有权转移附加条件，标的物风险与权属可能长期分离",
     "《民法典》第641条（所有权保留规则）",
     "明确所有权转移时点与风险转移的对应关系"),

    ("R14", "不可抗力定义过窄", "低",
     r"不可抗力[^。]{0,30}(仅指|限于|只包括)[^。]{0,50}$",
     "不可抗力定义过窄，弱势方难以援引免责",
     "《民法典》第180条、第590条（不可抗力法定含义）",
     "采用法定定义：“不能预见、不能避免且不能克服的客观情况”"),

    ("R15", "单方解释权", "中",
     r"(解释权[^，。]*(归|属于)|最终解释权)",
     "条款最终解释权归一方，争议时对方处于不利地位",
     "《民法典》第498条（格式条款有两种以上解释的，作不利于提供方的解释）",
     "删除解释权条款，约定“歧义按不利于提供方解释”"),

    ("R16", "保密期限缺失", "低",
     r"(保密(义务|责任|协议))((?![^。]{0,40}(期限|年内|年)))",
     "保密义务未约定期限，义务边界不清",
     "《反不正当竞争法》第9条（商业秘密保护）",
     "补充保密期限（如“合同终止后3年内持续有效”）"),
]


class RiskRuleEngine:
    """
    合同风险规则引擎（16 条内建规则）。

    用途:
        1. 主审 Agent 的确定性风险通道（规则 + LLM 双引擎）；
        2. 复核 Agent 的规则复检依据（approved/disputed 判定）。
    """

    def __init__(self):
        self.rules = [
            (rid, name, sev, re.compile(pattern, re.MULTILINE), desc, basis, sugg)
            for rid, name, sev, pattern, desc, basis, sugg in _RULES
        ]

    def check(self, clause_text: str) -> list[RuleHit]:
        """
        对单条条款执行全部规则。

        返回:
            命中的风险列表（RuleHit）
        """
        hits = []
        for rid, name, severity, pattern, desc, basis, sugg in self.rules:
            m = pattern.search(clause_text)
            if m:
                hits.append(RuleHit(
                    rule_id=rid, rule_name=name, severity=severity,
                    description=desc, legal_basis=basis, suggestion=sugg,
                    matched_text=m.group(0)[:60],
                ))
        return hits

    def check_contract(self, clauses: list[dict]) -> list[dict]:
        """
        对全部条款执行规则检查。

        返回:
            [{"clause_index", "clause_text", "risks": [RuleHit dict...],
               "risk_level": 最高等级}]（仅含有命中的条款）
        """
        results = []
        order = {"高": 3, "中": 2, "低": 1}
        for clause in clauses:
            hits = self.check(clause["text"])
            if hits:
                results.append({
                    "clause_index": clause["index"],
                    "clause_text": clause["text"][:200],
                    "risks": [asdict(h) for h in hits],
                    "risk_level": max((h.severity for h in hits),
                                      key=lambda s: order[s]),
                })
        return results

    def validate(self, risk: dict) -> dict:
        """
        复核校验：验证主审 Agent 识别的风险是否具备规则或法条依据。

        参数:
            risk: 主审产出的风险项（含 clause_text/severity/legal_basis）

        返回:
            {"valid": bool, "reason": "复核通过原因或异议说明"}
        """
        clause_text = risk.get("clause_text", "") or ""
        rule_hits = self.check(clause_text)
        claimed_severity = risk.get("risk_level") or risk.get("severity", "中")
        if not rule_hits:
            # 无规则命中：依赖 LLM 语义判断，置信降级但不直接否决
            return {"valid": True, "reason": "规则引擎未命中，采信语义审查结论（置信中等）"}
        # 有规则命中：等级比对
        max_rule_sev = max((h.severity for h in rule_hits),
                           key=lambda s: {"高": 3, "中": 2, "低": 1}[s])
        if max_rule_sev == claimed_severity:
            return {"valid": True, "reason": f"规则引擎命中并验证等级一致（{max_rule_sev}）"}
        return {"valid": False,
                "reason": f"等级不一致：主审判定{claimed_severity}，规则引擎判定{max_rule_sev}，建议复核"}

    def check_missed(self, clause_text: str) -> list[dict]:
        """
        遗漏检测：对主审未标记的条款做规则复扫（复核 Agent 专属）。

        返回:
            主审遗漏的规则级风险列表。
        """
        return [asdict(h) for h in self.check(clause_text)]


# 全局单例
risk_rule_engine = RiskRuleEngine()
