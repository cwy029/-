#!/usr/bin/env python3
"""复盘自动化报告：引擎预测 vs 实际赛果"""

import json, os, sys
from datetime import datetime

LOG_PATH = '/var/minis/skills/六扇门/results_log.jsonl'
SETTLE_PATH = '/var/minis/skills/六扇门/settlements.jsonl'


def _load_log():
    entries = []
    if not os.path.exists(LOG_PATH):
        return entries
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except:
                continue
            # 统一 name 字段
            if 'name' not in e and 'home' in e and 'away' in e:
                e['name'] = f"{e['home']} vs {e['away']}"
            elif 'name' not in e:
                continue
            # 统一 conclusion 字段（新格式用 conclusion，旧格式用 结论）
            if 'conclusion' not in e and '结论' in e:
                e['conclusion'] = e['结论']
            entries.append(e)
    return entries


def _load_settlements():
    entries = {}
    if not os.path.exists(SETTLE_PATH):
        return entries
    with open(SETTLE_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    s = json.loads(line)
                    entries[s['name']] = s
                except:
                    continue
    return entries


def _ah_outcome(bl, dir_ah, hg, ag):
    """亚盘结果"""
    if dir_ah == '+':
        net = hg - ag + bl
    else:
        net = ag - hg - bl
    if net > 0.25:
        return '全收'
    elif abs(net - 0.25) < 0.001:
        return '赢半'
    elif abs(net) < 0.001:
        return '走水'
    elif abs(net + 0.25) < 0.001:
        return '输半'
    else:
        return '全输'


def _ou_outcome(ou_line, hg, ag, recommended_side):
    """大小球结果"""
    total = hg + ag
    actual = '大球' if total > ou_line else '小球' if total < ou_line else '走水'
    if actual == '走水':
        return '走水'
    if actual == recommended_side:
        return '收'
    return '输'


def generate_report():
    """生成复盘报告"""
    log = _load_log()
    settled = _load_settlements()

    # 去重：每场比赛取最后一次预测
    latest = {}
    for e in log:
        name = e['name']
        # 跳过没有结论字段的旧数据
        conc = e.get('conclusion', e.get('结论'))
        if not conc:
            continue
        if name not in latest or e.get('ts', '') > latest[name].get('ts', ''):
            latest[name] = e

    # 匹配已结算
    matched = []
    for name, pred in latest.items():
        if name in settled:
            s = settled[name]
            matched.append((pred, s))

    if not matched:
        print('❌ 没有已结算的比赛可供复盘')
        return

    # 统计
    ah_correct = ah_total = 0
    ou_correct = ou_total = 0
    total_units = 0.0
    rows = []

    for pred, s in matched:
        name = pred['name']
        score = s.get('score', '')
        if not score or '-' not in score:
            continue
        try:
            hg, ag = [int(x) for x in score.split('-')]
        except:
            continue

        conc = pred.get('结论', pred.get('conclusion', '-'))
        dir_ah = pred.get('方向', pred.get('direction'))
        bl = pred.get('_bl', pred.get('bl'))
        ou_signal = pred.get('ou_signal', '')
        ou_line = pred.get('ou_line')

        # AH 结果
        ah_result = '-'
        ah_units = 0
        if dir_ah and bl is not None:
            ah_result = _ah_outcome(bl, dir_ah, hg, ag)
            if ah_result in ('全收',):
                ah_units = 1.0
                ah_correct += 1
            elif ah_result == '赢半':
                ah_units = 0.5
                ah_correct += 1
            elif ah_result == '输半':
                ah_units = -0.5
            elif ah_result == '全输':
                ah_units = -1.0
            # 走水 = 0
            if ah_result not in ('走水', '-'):
                ah_total += 1

        # OU 结果
        ou_result = '-'
        ou_units = 0
        if ou_signal and ou_line:
            ou_rec = ou_signal  # 引擎推荐方向
            ou_outcome = _ou_outcome(ou_line, hg, ag, ou_rec)
            if ou_outcome == '收':
                ou_units = 0.5
                ou_correct += 1
                ou_result = f'{ou_rec}✅'
            elif ou_outcome == '输':
                ou_units = -0.5
                ou_result = f'{ou_rec}❌'
            else:
                ou_result = f'{ou_rec}⚖️'
            if ou_outcome != '走水':
                ou_total += 1

        total_u = ah_units + ou_units
        total_units += total_u

        rows.append({
            'name': name,
            'score': score,
            'conc': conc if conc else '-',
            'ah': f'{ah_result}' if ah_result != '-' else '-',
            'ou': ou_result if ou_result != '-' else '-',
            'units': total_u,
        })

    # 输出报告
    w = 70
    print('')
    print('=' * w)
    print(f'  六扇门复盘报告 · {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * w)
    print(f'  共 {len(rows)} 场已结算')
    print()

    # 表格
    print(f'  {"比赛":<20s} {"比分":>5s} {"结论":<8s} {"AH":>6s} {"O/U":>8s} {"盈亏":>6s}')
    print(f'  {"─"*20} {"─"*5} {"─"*8} {"─"*6} {"─"*8} {"─"*6}')

    for r in rows:
        name_short = r['name'][:18]
        units_str = f'{r["units"]:+.1f}'
        conc_icon = {'EXECUTE': '✅', 'WATCHLIST': '🔶', 'PASS': '🚫'}.get(r['conc'], '❓')
        print(f'  {name_short:<20s} {r["score"]:>5s} {conc_icon}{r["conc"]:<8s} {r["ah"]:>6s} {r["ou"]:>8s} {units_str:>6s}')

    print()
    print('─' * w)

    # 汇总统计
    ah_rate = f'{ah_correct}/{ah_total} ({ah_correct/ah_total*100:.0f}%)' if ah_total else 'N/A'
    ou_rate = f'{ou_correct}/{ou_total} ({ou_correct/ou_total*100:.0f}%)' if ou_total else 'N/A'

    print(f'  📊 汇总')
    print(f'    亚盘正确率：{ah_rate}')
    print(f'    大小球正确率：{ou_rate}')
    print(f'    总盈亏：{total_units:+.1f}u')
    print()

    # 按结论分类
    for c in ['EXECUTE', 'WATCHLIST', 'PASS']:
        sub = [r for r in rows if r['conc'] == c]
        if sub:
            sub_units = sum(r['units'] for r in sub)
            wins = sum(1 for r in sub if r['units'] > 0)
            losses = sum(1 for r in sub if r['units'] < 0)
            print(f'    {c}（{len(sub)}场）：{wins}胜{losses}负 {sub_units:+.1f}u')

    print()
    print('=' * w)


if __name__ == '__main__':
    generate_report()
