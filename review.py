#!/usr/bin/env python3
"""复盘工具：记录赛果、计算盈亏、统计正确率"""

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
            # 统一 name 字段（新格式有 name，旧格式用 home vs away）
            if 'name' not in e and 'home' in e and 'away' in e:
                e['name'] = f"{e['home']} vs {e['away']}"
            elif 'name' not in e:
                continue
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
                s = json.loads(line)
                entries[s['name']] = s
    return entries


def _save_settlement(entry):
    with open(SETTLE_PATH, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def _calc_ah(bl, dir_ah, home_goals, away_goals):
    """计算亚盘盈亏"""
    if dir_ah == '+':
        net = home_goals - away_goals + bl
    else:
        net = away_goals - home_goals - bl
    if net > 0.25:
        return '全收', 1.0
    elif abs(net - 0.25) < 0.001:
        return '赢半', 0.5
    elif abs(net) < 0.001:
        return '走水', 0.0
    elif abs(net + 0.25) < 0.001:
        return '输半', -0.5
    else:
        return '全输', -1.0


def _calc_ou(ou_line, home_goals, away_goals, side):
    """计算大小球盈亏"""
    total = home_goals + away_goals
    if side == '大球':
        if total > ou_line:
            return '大球', 1.0
        elif total < ou_line:
            return '小球', -1.0
        else:
            return '走水', 0.0
    else:  # 小球
        if total < ou_line:
            return '小球', 1.0
        elif total > ou_line:
            return '大球', -1.0
        else:
            return '走水', 0.0


def list_pending():
    """列出未结算的预测（仅显示新格式，跳过旧 v2 数据）"""
    entries = _load_log()
    settled = _load_settlements()
    pending = [e for e in entries if e['name'] not in settled and e.get('conclusion')]
    if not pending:
        print('✅ 所有预测已结算')
        return
    print(f'📋 未结算预测（{len(pending)} 条）')
    print(f'{"":─<60}')
    for e in pending:
        td = e.get('交易决策', {})
        product = td.get('product', '-') if td else '-'
        size = td.get('size', '') if td else ''
        print(f'  {e["name"]}')
        print(f'    系统结论: {e.get("conclusion", "-")}  | 推荐: {product}  | 仓位: {size}')
        print()


def settle(name, score):
    """结算一场比赛"""
    entries = _load_log()
    settled = _load_settlements()
    entry = None
    for e in entries:
        if e['name'] == name:
            entry = e  # 取最后一条（最新分析）
    if not entry:
        print(f'❌ 未找到分析记录: {name}')
        return
    if name in settled:
        print(f'⚠️ {name} 已结算，跳过')
        return

    # 解析比分
    try:
        parts = score.replace(' ', '').split(':')
        if len(parts) != 2:
            parts = score.split('-')
        hg = int(parts[0])
        ag = int(parts[1])
    except:
        print(f'❌ 比分格式错误，请用 "2-1" 或 "2:1"')
        return

    result = {
        'name': name,
        'score': f'{hg}-{ag}',
        'ts': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'ah_result': '-',
        'ah_units': 0,
        'ou_result': '-',
        'ou_units': 0,
        'total_units': 0,
    }

    td = entry.get('交易决策', {})

    # 亚盘结算
    bl = entry.get('bl')
    dir_ah = entry.get('direction') or entry.get('方向')
    product = td.get('product', '') if td else ''

    if bl is not None and dir_ah in ('+', '-'):
        ah_res, ah_u = _calc_ah(bl, dir_ah, hg, ag)
        result['ah_result'] = ah_res
        result['ah_units'] = ah_u
        if 'OU' in product or '大' in product or '小' in product:
            result['ah_units'] = round(ah_u * 1.0, 2)
        else:
            result['ah_units'] = round(ah_u * 1.5, 2)

    # 大小球结算
    ou_signal = entry.get('ou_signal', '')
    ou_line = entry.get('ou_line')
    if ou_signal in ('大球', '小球') and ou_line:
        ou_res, ou_u = _calc_ou(ou_line, hg, ag, ou_signal)
        result['ou_result'] = ou_res
        result['ou_units'] = round(ou_u * 0.5, 2)

    result['total_units'] = round(result['ah_units'] + result['ou_units'], 2)

    # 显示
    print(f'\n📊 {name}')
    print(f'  赛果: {hg}-{ag}')
    if result['ah_result'] != '-':
        print(f'  AH: {result["ah_result"]} ({result["ah_units"]:+.1f}u)')
    if result['ou_result'] != '-':
        print(f'  OU: {result["ou_result"]} ({result["ou_units"]:+.1f}u)')
    print(f'  合计: {result["total_units"]:+.1f}u')

    _save_settlement(result)


def stats():
    """统计汇总"""
    settled = _load_settlements()
    if not settled:
        print('暂无结算记录')
        return

    total = len(settled)
    ah_wins = ah_losses = ah_pushes = 0
    ou_wins = ou_losses = ou_pushes = 0
    total_units = 0.0

    for name, s in sorted(settled.items()):
        total_units += s['total_units']

        if s['ah_result'] in ('全收', '赢半'):
            ah_wins += 1
        elif s['ah_result'] in ('全输', '输半'):
            ah_losses += 1
        elif s['ah_result'] == '走水':
            ah_pushes += 1

        if s['ou_result'] in ('大球', '小球'):
            # ou_result 是赛果方向，不是预测方向。
            # 通过 ou_units > 0 判断是否猜对
            if s['ou_units'] > 0:
                ou_wins += 1
            else:
                ou_losses += 1
        elif s['ou_result'] == '走水':
            ou_pushes += 1

    print(f'\n📈 复盘统计（共 {total} 场）')
    print(f'{"":─<50}')
    total_ah = ah_wins + ah_losses
    if total_ah > 0:
        print(f'  AH: {ah_wins}胜 {ah_losses}负 {ah_pushes}走  胜率 {ah_wins/total_ah*100:.0f}%')
    total_ou = ou_wins + ou_losses
    if total_ou > 0:
        print(f'  OU: {ou_wins}胜 {ou_losses}负 {ou_pushes}走  胜率 {ou_wins/total_ou*100:.0f}%')
    print(f'  总盈亏: {total_units:+.1f}u')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法:')
        print('  python3 review.py list        — 列出未结算预测')
        print('  python3 review.py settle "比赛名" "2-1"  — 结算一场')
        print('  python3 review.py stats       — 统计汇总')
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'list':
        list_pending()
    elif cmd == 'settle':
        if len(sys.argv) < 4:
            print('用法: review.py settle "比赛名" "比分"')
            sys.exit(1)
        settle(sys.argv[2], sys.argv[3])
    elif cmd == 'stats':
        stats()
    else:
        print(f'未知命令: {cmd}')
