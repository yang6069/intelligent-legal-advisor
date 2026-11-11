# -*- coding: utf-8 -*-
"""临时脚本：修复 risk_rules.py 中嵌套 ASCII 引号（执行后删除）。"""
import io

P = "src/contract/risk_rules.py"
s = io.open(P, encoding="utf-8").read()

fixes = [
    # (旧文本 ASCII 引号, 新文本全角引号)
    ('改为"价格调整需双方书面确认"或设置调整上限与通知期',
     '改为“价格调整需双方书面确认”或设置调整上限与通知期'),
    ('设置"续约需提前30日书面确认"，删除"未异议视为续约"',
     '设置“续约需提前30日书面确认”，删除“未异议视为续约”'),
    ('采用法定定义："不能预见、不能避免且不能克服的客观情况"',
     '采用法定定义：“不能预见、不能避免且不能克服的客观情况”'),
    ('删除解释权条款，约定"歧义按不利于提供方解释"',
     '删除解释权条款，约定“歧义按不利于提供方解释”'),
    ('补充保密期限（如"合同终止后3年内持续有效"）',
     '补充保密期限（如“合同终止后3年内持续有效”）'),
    ('删除免责条款，或改为"按过错比例分担责任"',
     '删除免责条款，或改为“按过错比例分担责任”'),
    ('改为"被告住所地或合同履行地法院管辖"或约定中立仲裁机构',
     '改为“被告住所地或合同履行地法院管辖”或约定中立仲裁机构'),
    ('补充"按月支付不低于离职前12个月平均工资30%的补偿"',
     '补充“按月支付不低于离职前12个月平均工资30%的补偿”'),
    ('限定赔偿范围："以直接实际损失为限，且不超过合同总额X%"',
     '限定赔偿范围：“以直接实际损失为限，且不超过合同总额X%”'),
]

n = 0
for old, new in fixes:
    if old in s:
        s = s.replace(old, new)
        n += 1

io.open(P, "w", encoding="utf-8").write(s)
print(f"fixed {n} of {len(fixes)} patterns")

# 校验：编译通过
import py_compile
try:
    py_compile.compile(P, doraise=True)
    print("COMPILE OK")
except py_compile.PyCompileError as e:
    print("COMPILE FAIL:", e)
