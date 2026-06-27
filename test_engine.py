#!/usr/bin/env python3
"""六扇门 V8 回归测试（适配 GitHub 最新版）"""

import sys, json
sys.path.insert(0, '.')
import football_analyzer as fa

passed = 0
failed = 0

def ok(name, cond, msg=''):
    global passed, failed
    if cond:
        passed += 1
        print(f'✓ {name}')
    else:
        failed += 1
        print(f'✗ {name} {msg}')

# 1. 中文盘口解析
cases = {
    '平手': 0.0,
    '半球': -0.5,
    '受半球': 0.5,
    '一球': -1.0,
    '受一球': 1.0,
    '平/半': -0.25,
    '受两球半': 2.5,
    '两球': -2.0,
}
all_ok = all(fa._parse_hcp(k) == v for k, v in cases.items())
ok('_parse_hcp Chinese handicap', all_ok)

# 2. Markdown 解析
with open('/tmp/6s_user_input.md') as f:
    raw = f.read()
matches = fa._parse_md(raw)
ok('markdown parse 4 matches', len(matches) == 4)

# 3. 四场统计决策
expected = {
    '土耳其 vs 美国': 'EXECUTE',
    '巴拉圭 vs 澳大利亚': 'PASS',
    '日本 vs 瑞典': 'WATCHLIST',
    '突尼斯 vs 荷兰': 'EXECUTE',
}
for m in matches:
    name = m['name']
    r = fa.analyze(name, m)
    exp = expected.get(name)
    if exp:
        ok(f'{name} -> {exp}', r['结论'] == exp, f"got {r['结论']}")

# 4. 价格合理性
turkey = next(m for m in matches if '土耳其' in m['name'])
r = fa.analyze(turkey['name'], turkey)
ok('Turkey price reasonable', r['价格'] == '合理')

# 5. BC 温度
ok('Turkey BC no objection', r['BC判决'] == '无异议')

# 6. 大小球解析
ok('Turkey OU has signal', r['大小球'] != '')

# 7. 结论字段存在
ok('result has 结论', '结论' in r)

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)