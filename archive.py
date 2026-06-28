#!/usr/bin/env python3
"""
六扇门赔率存档与复盘系统

用法:
  python3 archive.py run <input.md|input.json>     运行引擎并归档输入+输出
  python3 archive.py result <match_id> <actual>    录入实际赛果并更新总账
  python3 archive.py list                          列出所有归档记录

归档结构:
  archive/inputs/  原始赔率文件（按日期命名）
  archive/outputs/ 引擎输出结果（JSON + 文本）
  archive/results/ 赛果录入
  archive/ledger.csv 总账
"""

import sys, os, json, csv, shutil, re, io
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
# 赔率存档放在共享目录，避免随 GitHub 仓库公开
ARCHIVE = Path('/var/minis/shared/六扇门_archive')
INPUTS = ARCHIVE / 'inputs'
OUTPUTS = ARCHIVE / 'outputs'
RESULTS = ARCHIVE / 'results'
LEDGER = ARCHIVE / 'ledger.csv'


def ensure_dirs():
    for d in (INPUTS, OUTPUTS, RESULTS):
        d.mkdir(parents=True, exist_ok=True)


def sanitize(s):
    return re.sub(r'[^\w\-]+', '_', s).strip('_')


def load_engine():
    sys.path.insert(0, str(BASE))
    import football_analyzer as fa
    return fa


def parse_input_file(path):
    fa = load_engine()
    with open(path, 'r', encoding='utf-8') as f:
        raw = f.read()
    # 优先 JSON
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return [data]
        return data
    except json.JSONDecodeError:
        pass
    return fa._parse_md(raw)


def run_and_archive(input_path):
    ensure_dirs()
    fa = load_engine()
    matches = parse_input_file(input_path)
    if not matches:
        print('没有解析到比赛')
        return

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    batch_id = f'batch_{ts}'

    # 1. 归档输入
    ext = Path(input_path).suffix
    archived_input = INPUTS / f'{batch_id}{ext}'
    shutil.copy2(input_path, archived_input)

    # 2. 运行并归档输出
    records = []
    for m in matches:
        name = m.get('name', 'unknown')
        r = fa.analyze(name, m)

        # 文本报告
        txt_path = OUTPUTS / f'{ts}_{sanitize(name)}.txt'
        buf = io.StringIO()
        with redirect_stdout(buf):
            fa._print(r, name)
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(buf.getvalue())

        # JSON
        json_path = OUTPUTS / f'{ts}_{sanitize(name)}.json'
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(r, f, ensure_ascii=False, indent=2)

        # 总账记录
        ta = r.get('交易决策', {}) or {}
        record = {
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'batch_id': batch_id,
            'match': name,
            'input_file': str(archived_input.relative_to(ARCHIVE)),
            'output_txt': str(txt_path.relative_to(ARCHIVE)),
            'output_json': str(json_path.relative_to(ARCHIVE)),
            'system_conc': r.get('结论', ''),
            'system_direction': r.get('方向', ''),
            'system_line': str(r.get('_bl', '')),
            'system_ou': r.get('大小球结论', ''),
            'trader_side': ta.get('trade_side', ''),
            'trader_product': ta.get('product', ''),
            'trader_size': ta.get('size', ''),
            'actual_result': '',
            'pnl': '',
            'notes': ''
        }
        records.append(record)

    # 3. 写入总账
    write_ledger(records)

    # 4. 终端输出
    for rec in records:
        print(f"{rec['match']}  系统:{rec['system_conc']}  交易员:{rec['trader_side']}  (归档: {rec['output_txt']})")


def write_ledger(records):
    fieldnames = ['date', 'batch_id', 'match', 'input_file', 'output_txt', 'output_json',
                  'system_conc', 'system_direction', 'system_line', 'system_ou',
                  'trader_side', 'trader_product', 'trader_size',
                  'actual_result', 'pnl', 'notes']
    write_header = not LEDGER.exists()
    with open(LEDGER, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        w.writerows(records)


def list_records():
    if not LEDGER.exists():
        print('总账为空')
        return
    with open(LEDGER, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f'共 {len(rows)} 条记录')
    for r in rows:
        status = f"[{r['actual_result']}]" if r.get('actual_result') else '[待结算]'
        print(f"{r['date']} {r['match']:20s}  系统:{r.get('system_conc','?'):10s}  交易员:{r.get('trader_side','?'):10s} {status}")


def update_result(match_query, actual_result, pnl='', notes=''):
    if not LEDGER.exists():
        print('总账为空')
        return

    with open(LEDGER, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    fieldnames = rows[0].keys() if rows else []
    updated = []
    found = False
    for r in rows:
        if match_query in r['match'] and not r['actual_result']:
            r['actual_result'] = actual_result
            if pnl:
                r['pnl'] = pnl
            if notes:
                r['notes'] = notes
            found = True
            print(f"已更新: {r['match']} -> {actual_result}")
        updated.append(r)

    if not found:
        print(f'未找到匹配且未结算的记录: {match_query}')
        return

    with open(LEDGER, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(updated)


def main():
    ensure_dirs()
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == 'run':
        if len(sys.argv) < 3:
            print('请提供输入文件路径')
            return
        run_and_archive(sys.argv[2])
    elif cmd == 'list':
        list_records()
    elif cmd == 'result':
        if len(sys.argv) < 4:
            print('用法: archive.py result <比赛名关键词> <实际结果> [pnl] [notes]')
            return
        update_result(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else '', sys.argv[5] if len(sys.argv) > 5 else '')
    else:
        print(__doc__)


if __name__ == '__main__':
    main()