#!/usr/bin/env python3
"""
V8 规则引擎

公司职责（写死）：
  Pinnacle    → 专业市场、价格锚、公平价基准
  Bet365      → 大众市场、方向投票
  singbet → 亚洲盘口确认、结构验证
  澳门彩票     → 欧赔验证
  庄家精简为3家，威廉和澳门移除

流程：
  ① 找方向
  ② 看市场有没有形成共识（亚盘+欧赔+平局）
  ③ 看这个变化有没有被其它公司确认（成交确认/价格）
  ④ 致命否决
  → EXECUTE
"""

import json, sys, re

BK_CORE = ['Pinnacle', 'Bet365', 'singbet']
BK_EURO = ['Pinnacle', 'Bet365', 'singbet']
BK_LABEL = {'Pinnacle': 'Pin', 'Bet365': '365', 'singbet': 'Crown',
            '澳门彩票': '澳门'}
BK_FULL = {'Pinnacle': 'Pinnacle', 'Bet365': 'Bet365', 'singbet': 'Crown',
           '澳门彩票': '澳门彩票'}

def _fl(v):
    if v is None:
        return None
    v = str(v).strip()
    # 去掉 (↑) (↓) 等升降标记
    if '(' in v:
        v = v[:v.index('(')].strip()
    # 去掉尾部升降箭头（如 1.98 ↓）
    v = re.sub(r'\s*[↑↓]+\s*$', '', v).strip()
    if not v:
        return None
    return float(v)


def _ld(ol, cl):
    if ol is None or cl is None:
        return None
    d = round(cl - ol, 2)
    if abs(d) < 0.05:
        return None
    return 'away' if d > 0 else 'home'


def sgn(d):
    return '+' if d == 'home' else '-' if d == 'away' else '0'


def _gs(bk, src):
    d_ = src.get(bk, {}).get('Spread', {})
    return next(iter(d_.values()), {}) if len(d_) == 1 else {}


def _gm(bk, src):
    d_ = src.get(bk, {}).get('ML', {})
    return next(iter(d_.values()), {}) if len(d_) == 1 else {}


def _rw(v):
    if v is None:
        return ''
    return f'{v:+.3f}' if abs(v) < 0.1 else f'{v:+.2f}'


def _pick_deepest_line(src, dir_ah):
    """只在核心亚盘三家（Pin/365/singbet）中挑选最深线位
    澳门/威廉职责为欧赔验证，不参与亚盘线位决策
    """
    bl = None
    for bk in BK_CORE:
        sp = src.get(bk, {}).get('Spread', {})
        sp_v = next(iter(sp.values()), {}) if len(sp) == 1 else {}
        ln = _fl(sp_v.get('line'))
        if ln is not None:
            if dir_ah == '+' and (bl is None or ln < bl):
                bl = ln
            elif dir_ah == '-' and (bl is None or ln > bl):
                bl = ln
    return bl


# ═══════════════════════════════════════════
#  ① 找方向
# ═══════════════════════════════════════════

def get_direction(mkt):
    """Pin/365/singbet 三家亚盘投票
    if 有变化 → 分析Direction（变化）
    else → 分析State（静态线位）
    返回 (dir_ah, quality, signals, dissent_bk, active, mode)
    """
    signals = {}
    for bk in BK_CORE:
        signals[bk] = sgn(_ld(_fl(_gs(bk, mkt['snap']).get('line')),
                               _fl(_gs(bk, mkt['curr']).get('line'))))

    votes_for = sum(1 for s in signals.values() if s == '+')
    votes_agst = sum(1 for s in signals.values() if s == '-')
    active = votes_for + votes_agst

    # 有变动 → 按变动投票
    if active > 0:
        if votes_for > votes_agst:
            dir_ah = '+'; maj = votes_for; min_ = votes_agst
        elif votes_agst > votes_for:
            dir_ah = '-'; maj = votes_agst; min_ = votes_for
        else:
            # 1-1 平票，方向不明确
            sigs = ' '.join(f'{BK_LABEL[b]}{signals[b]}' for b in BK_CORE)
            return None, None, signals, None, active, 'change'

        dissent_bk = None
        if maj == 3:
            qual = '一致'
        elif maj == 2 and min_ == 1:
            qual = '分歧'
            for bk in BK_CORE:
                if signals[bk] != '0' and signals[bk] != dir_ah:
                    dissent_bk = bk; break
        elif maj == 1 and min_ == 0:
            qual = '偏弱'
        else:
            qual = '分歧'
        return dir_ah, qual, signals, dissent_bk, active, 'change'

    # 无变动 → 无方向（取消静态线位指向，避免天然推上盘）
    # 线位没有变化时，三家投票都是0，直接返回无方向
    sigs = ' '.join(f'{BK_LABEL[b]}{signals[b]}' for b in BK_CORE)
    return None, None, signals, None, 0, None


# ═══════════════════════════════════════════
#  ② 共识检查
# ═══════════════════════════════════════════

def check_structure(signals, dir_ah, pin_has_data):
    """亚盘结构一致性"""
    p_s = signals.get('Pinnacle', '0')
    b_s = signals.get('Bet365', '0')
    s_s = signals.get('singbet', '0')

    if p_s == '0' and not pin_has_data:
        return 'WATCHLIST', 'Pin无数据'
    if p_s != '0' and b_s != '0' and p_s != b_s:
        return 'PASS', f'Pin{p_s} vs 365{b_s}相反'
    if p_s != '0' and b_s != '0' and p_s == b_s and s_s != '0' and s_s != p_s:
        return 'PASS', f'singbet{s_s}与Pin{p_s}/365{b_s}相反'
    return 'OK', None


def euro_verdict(bk, mkt, dir_ah):
    """单家公司欧赔是否支持盘口方向"""
    snap_ml = _gm(bk, mkt['snap'])
    curr_ml = _gm(bk, mkt['curr'])
    if not snap_ml or not curr_ml:
        return None
    fav = 'home' if dir_ah == '+' else 'away'
    snap_o = _fl(snap_ml.get(fav))
    curr_o = _fl(curr_ml.get(fav))
    if not snap_o or not curr_o:
        return None
    chg = (snap_o - curr_o) / snap_o
    if chg >= 0.01:
        return '支持'
    elif chg <= -0.01:
        return '反对'
    return '中性'


def check_convergence(mkt):
    """测量5家亚盘线位从初盘到现盘的收敛程度
    返回 (convergence, snap_rng, curr_rng, bk_lines)
      convergence > 0  = 市场收敛，分歧缩小
      convergence < 0  = 市场发散，分歧扩大
    """
    snap_l = []
    curr_l = []
    bk_l = []

    for bk in BK_CORE:
        ss = mkt.get('snap', {}).get(bk, {}).get('Spread', {})
        sv = next(iter(ss.values()), {}) if len(ss) == 1 else {}
        sl = _fl(sv.get('line'))
        cs = mkt.get('curr', {}).get(bk, {}).get('Spread', {})
        cv = next(iter(cs.values()), {}) if len(cs) == 1 else {}
        cl = _fl(cv.get('line'))
        if sl is not None:
            snap_l.append(sl)
        if cl is not None:
            curr_l.append(cl)
        if sl is not None or cl is not None:
            bk_l.append((BK_LABEL.get(bk, bk), sl, cl))

    if len(snap_l) < 2 or len(curr_l) < 2:
        return None, None, None, bk_l

    sr = round(max(snap_l) - min(snap_l), 2)
    cr = round(max(curr_l) - min(curr_l), 2)
    cv = round(sr - cr, 2)

    return cv, sr, cr, bk_l


def check_euro(mkt, dir_ah):
    """5家欧赔综合：严格多数决，支持=反对时优先判中性/反对"""
    results = {}
    for bk in BK_EURO:
        v = euro_verdict(bk, mkt, dir_ah)
        if v is not None:
            results[bk] = v
    sup = sum(1 for v in results.values() if v == '支持')
    neut = sum(1 for v in results.values() if v == '中性')
    opp = sum(1 for v in results.values() if v == '反对')
    total = sum([sup, neut, opp])
    if total == 0:
        return '中性', results

    # 绝对多数（≥3家）直接定调
    if opp >= 3:
        return '反对', results
    if sup >= 3:
        return '支持', results
    if neut >= 3:
        return '中性', results

    # 没有绝对多数时，按支持 vs 反对判定；相等或无法分出明显优势 → 中性
    if sup > opp and sup > neut:
        return '支持', results
    if opp > sup and opp > neut:
        return '反对', results
    return '中性', results


def check_draw_signal(mkt, dir_ah):
    """平局翻转检测
    条件A：方向方↓ + 平赔↓ + 对手方↑（任一公司触发）
    条件B：方向为让球方 + 平赔骤降≥3家
    """
    fav_key = 'home' if dir_ah == '+' else 'away'
    opp_key = 'away' if dir_ah == '+' else 'home'
    
    # 条件A：经典翻转
    for bk in BK_EURO:
        s_ml = _gm(bk, mkt['snap'])
        c_ml = _gm(bk, mkt['curr'])
        if not s_ml or not c_ml:
            continue
        try:
            fs, fc = _fl(s_ml.get(fav_key)), _fl(c_ml.get(fav_key))
            ds, dc = _fl(s_ml.get('draw')), _fl(c_ml.get('draw'))
            os_, oc_ = _fl(s_ml.get(opp_key)), _fl(c_ml.get(opp_key))
        except:
            continue
        if not all([fs, fc, ds, dc, os_, oc_]):
            continue
        if fc < fs and dc < ds and abs(dc - ds) >= 0.03 and oc_ > os_:
            return True, BK_LABEL.get(bk, bk), 'A'
    
    # 条件B：方向让球方 + 平赔骤降
    # 注意：让球方既可能是主队(+)也可能是客队(-)
    drop_count = 0
    for bk in BK_EURO:
        s_ml = _gm(bk, mkt['snap'])
        c_ml = _gm(bk, mkt['curr'])
        if not s_ml or not c_ml:
            continue
        ds = _fl(s_ml.get('draw'))
        dc = _fl(c_ml.get('draw'))
        if ds and dc and (ds - dc) / ds >= 0.04:
            drop_count += 1
    if drop_count >= 3:
        return True, None, 'B'
    
    return False, None, None


# ═══════════════════════════════════════════
#  ③ 价格确认（Price Confirmation）
#  以 Pin 公平价为锚，判断当前价格能不能成交
# ═══════════════════════════════════════════

def check_price(mkt, dir_ah, price_side=None):
    """③ 价格确认 v2 — 用价差变化代替绝对水位比较
    软庄天生比 Pin 低是常态，不是信号。
    只看「软庄与 Pin 的价差从初盘到现盘变没变」：
      - 策略A：snap/curr 都与 Pin 同线位 → 比价差变化（ΔΔ）
      - 策略B：自身线位稳定（snap线=curr线，未跟Pin推盘）→ 比自身水位变化（Δ）
      - 其他：跳过（不可比）
    价差拉大 → 追价；价差不变/缩小 → 正常跟随
    追价 ≥3家 → 偏贵；否则 → 合理
    """
    bl = _pick_deepest_line(mkt['curr'], dir_ah)
    if bl is None:
        return '合理', '无线位'

    wkey = price_side or ('home' if dir_ah == '+' else 'away')

    pin_curr_sp = next(iter(mkt.get('curr', {}).get('Pinnacle', {}).get('Spread', {}).values()), {})
    pin_snap_sp = next(iter(mkt.get('snap', {}).get('Pinnacle', {}).get('Spread', {}).values()), {})
    pin_curr_w = _fl(pin_curr_sp.get(wkey))
    pin_snap_w = _fl(pin_snap_sp.get(wkey))
    pin_curr_line = _fl(pin_curr_sp.get('line'))
    pin_snap_line = _fl(pin_snap_sp.get('line'))

    if pin_curr_w is None:
        return '合理', 'Pin无现盘水'

    chasing = 0
    following = 0
    detail = []

    for bk in ['Bet365', 'singbet']:
        curr_sp = next(iter(mkt.get('curr', {}).get(bk, {}).get('Spread', {}).values()), {})
        snap_sp = next(iter(mkt.get('snap', {}).get(bk, {}).get('Spread', {}).values()), {})
        curr_l  = _fl(curr_sp.get('line'))
        snap_l  = _fl(snap_sp.get('line'))
        curr_w  = _fl(curr_sp.get(wkey))
        snap_w  = _fl(snap_sp.get(wkey))

        if curr_w is None or snap_w is None:
            continue

        label = BK_LABEL.get(bk, bk)
        used = None

        # 策略A：两端都与 Pin 同线位 → 价差变化
        if (pin_curr_line is not None and curr_l is not None and abs(curr_l - pin_curr_line) < 0.01
                and pin_snap_line is not None and snap_l is not None and abs(snap_l - pin_snap_line) < 0.01
                and pin_snap_w is not None):
            gap_s = round(snap_w - pin_snap_w, 2)
            gap_c = round(curr_w - pin_curr_w, 2)
            delta = round(gap_c - gap_s, 2)
            used = 'A'
        # 策略B：自身线位稳定 → 自身水位变化
        elif snap_l is not None and curr_l is not None and abs(curr_l - snap_l) < 0.01:
            delta = round(curr_w - snap_w, 2)
            gap_s = snap_w
            gap_c = curr_w
            used = 'B'

        if used is None:
            continue

        if delta <= -0.02:
            chasing += 1
            detail.append(f'{label}追价({gap_s:.2f}→{gap_c:.2f},Δ{_rw(delta)})')
        else:
            following += 1
            detail.append(f'{label}跟随({gap_s:.2f}→{gap_c:.2f},Δ{_rw(delta)})')

    summary = f'Pin{pin_curr_w:.2f} ' + ' '.join(detail) if detail else f'Pin{pin_curr_w:.2f} 无可比'
    if chasing >= 3:
        return '偏贵', summary
    return '合理', summary


# ═══════════════════════════════════════════
#  提示项（信息，不影响结论）
# ═══════════════════════════════════════════

def check_ev(mkt, dir_ah):
    bl_now = _pick_deepest_line(mkt['curr'], dir_ah)
    bl_snap = _pick_deepest_line(mkt['snap'], dir_ah)
    if bl_now is None or bl_snap is None:
        return None
    if dir_ah == '+':
        return ('Early Value',) if bl_snap < bl_now else ('Current Price',)
    return ('Early Value',) if bl_snap > bl_now else ('Current Price',)


def check_water(mkt, dir_ah):
    """检测同线位水位变化，只在核心亚盘三家中找匹配线位
    避免第一家 snap 线位不匹配就提前退出
    """
    bl = _pick_deepest_line(mkt['curr'], dir_ah)
    if bl is None:
        return None
    wkey = 'home' if dir_ah == '+' else 'away'
    candidates = []
    for bk in BK_CORE:
        sp = mkt.get('curr', {}).get(bk, {}).get('Spread', {})
        sp_v = next(iter(sp.values()), {}) if len(sp) == 1 else {}
        ln = _fl(sp_v.get('line'))
        if ln is None or abs(ln - bl) >= 0.01:
            continue
        snap_sp = mkt.get('snap', {}).get(bk, {}).get('Spread', {})
        snap_v = next(iter(snap_sp.values()), {}) if len(snap_sp) == 1 else {}
        snap_ln = _fl(snap_v.get('line'))
        if snap_ln is None or abs(snap_ln - bl) >= 0.01:
            continue
        sn_o = _fl(snap_v.get(wkey))
        cu_o = _fl(sp_v.get(wkey))
        if sn_o and cu_o:
            candidates.append((cu_o - sn_o, bk))

    if not candidates:
        return None
    # 取水位变化最大的那家作为代表
    diff, bk = max(candidates, key=lambda x: abs(x[0]))
    label = BK_LABEL.get(bk, bk)
    if diff <= 0:
        return (f'资金支持（{label}{_rw(diff)}）',)
    return (f'资金流出（{label}{_rw(diff)}）',)


def _ou_vote(bk, mkt):
    """对单个庄家的大小球信号投票，返回 (方向, 描述, 线位, 大球水, 小球水) 或 None"""
    cv = next(iter(mkt.get('curr', {}).get(bk, {}).get('Totals', {}).values()), None)
    ov = next(iter(mkt.get('snap', {}).get(bk, {}).get('Totals', {}).values()), None)
    if cv is None or ov is None:
        return None
    cl = _fl(cv.get('line'))
    ol = _fl(ov.get('line'))
    ch = _fl(cv.get('home'))
    oh = _fl(ov.get('home'))
    cu = _fl(cv.get('under'))
    ou_ = _fl(ov.get('under'))
    if cl is None or ol is None:
        return None

    if cl > ol:
        if ch is not None and oh is not None and ch < oh:
            return '大球', '升盘+大球水降', cl, ch, cu
        elif ch is not None and oh is not None and ch > oh:
            return '小球', '升盘+大球水升诱盘', cl, ch, cu
        return '大球', '升盘', cl, ch, cu
    elif cl < ol:
        if cu is not None and ou_ is not None and cu < ou_:
            return '小球', '退盘+小球水降', cl, ch, cu
        elif cu is not None and ou_ is not None and cu > ou_:
            return '大球', '退盘+小球水升诱盘', cl, ch, cu
        return '小球', '退盘', cl, ch, cu
    else:
        if ch and oh and ch > oh:
            return '小球', '线位不动+大球水升', cl, ch, cu
        if ch and oh and ch < oh:
            return '大球', '线位不动+大球水降', cl, ch, cu
        if cu and ou_ and cu < ou_:
            return '小球', '线位不动+小球水降', cl, ch, cu
        if cu and ou_ and cu > ou_:
            return '大球', '线位不动+小球水升', cl, ch, cu
        return '中性', '线位水位未动', cl, ch, cu


def check_ou(mkt):
    """大小球：三家交叉投票，取多数决。
    返回 (方向, 说明)
    """
    votes = []
    details = []
    bk_order = ['Pinnacle', 'Bet365', 'singbet']
    bk_labels = {'Pinnacle': 'Pin', 'Bet365': '365', 'singbet': 'Crown'}

    for bk in bk_order:
        v = _ou_vote(bk, mkt)
        if v is not None:
            direction, desc, cl, ch, cu = v
            votes.append(direction)
            details.append(f'{bk_labels[bk]}{desc}')

    if not votes:
        return None, None

    over_count = votes.count('大球')
    under_count = votes.count('小球')
    neutral_count = votes.count('中性')
    desc = '｜'.join(details)

    if over_count >= 2 and under_count == 0:
        return '大球', f'{desc}'
    elif under_count >= 2 and over_count == 0:
        return '小球', f'{desc}'
    elif over_count >= 2:
        # 多数大球，少数反对
        return '大球', f'{desc}（{over_count}家大球,{under_count}家反对）'
    elif under_count >= 2:
        return '小球', f'{desc}（{under_count}家小球,{over_count}家反对）'
    elif over_count == under_count:
        return '中性', f'分歧：{desc}'
    elif over_count == 1 and under_count == 0 and neutral_count == 0:
        # 仅1家有数据
        return votes[0], details[0]
    else:
        return '中性', f'分歧：{desc}'


def check_draw_drop(mkt):
    """平赔骤降预警"""
    count = 0
    bks = []
    for bk in BK_EURO:
        s_ml = _gm(bk, mkt['snap'])
        c_ml = _gm(bk, mkt['curr'])
        if not s_ml or not c_ml:
            continue
        ds = _fl(s_ml.get('draw'))
        dc = _fl(c_ml.get('draw'))
        if ds and dc:
            if dc < ds:
                count += 1
                bks.append(BK_LABEL.get(bk, bk))
    if count >= 3:
        return (f'⚠️平赔骤降（{" ".join(bks)}）',)
    return None


# ═══════════════════════════════════════════
#  ④ 风控否决（Risk Filter）
# ═══════════════════════════════════════════

def check_risk_filter(mkt, dir_ah, active, qual, price_v, euro_v, flipped, signals=None):
    """独立风控层：在结构/欧赔/价格之后做最终风险过滤
    R1-R3 延续，R4/R5 从原结构验证移入
    返回 (结论, 原因) 或 None
    """
    wkey = 'home' if dir_ah == '+' else 'away'

    # 判断方向方是让球方还是受让方
    # 线位<0=主队让球(主队是让球方) 线位>0=客队让球(客队是让球方)
    ref_line = None
    for bk in BK_CORE:
        ln = _fl(_gs(bk, mkt['curr']).get('line'))
        if ln is not None:
            ref_line = ln
            break
    dir_on_fav = False  # 方向方是让球方？
    if ref_line is not None and ref_line != 0:
        if dir_ah == '+' and ref_line < 0:
            dir_on_fav = True
        elif dir_ah == '-' and ref_line > 0:
            dir_on_fav = True

    # ═══════════════════════════════════════════
    #  风控否决（三条硬过滤器，不参与方向判断）
    #
    #  R4 结构冲突   — Pin(专业) ≠ 365(大众) → 市场无共识 → PASS
    #  R5 亚洲盘口   — Pin=365 但Crown反向 → 亚洲不给确认 → PASS
    #  R3 价格否决   — 偏贵 + 方向弱(无追价) → 不值 → PASS
    #
    #  三者职责清晰、不重叠、不打架
    # ═══════════════════════════════════════════

    # R4: 结构冲突 — 专业 vs 大众意见分裂
    if signals:
        p_s = signals.get('Pinnacle', '0')
        b_s = signals.get('Bet365', '0')
        s_s = signals.get('singbet', '0')
        pin_has = ('Spread' in mkt.get('curr', {}).get('Pinnacle', {}) or
                   'Spread' in mkt.get('snap', {}).get('Pinnacle', {}))
        if p_s == '0' and not pin_has:
            return 'WATCHLIST', 'Pin无数据'
        if p_s != '0' and b_s != '0' and p_s != b_s:
            return 'PASS', f'结构否决：Pin{p_s} vs 365{b_s}相反'

    # R5: 亚洲盘口否决 — 专业+大众一致，但Crown反向
    if signals:
        p_s = signals.get('Pinnacle', '0')
        b_s = signals.get('Bet365', '0')
        s_s = signals.get('singbet', '0')
        if p_s != '0' and b_s != '0' and p_s == b_s and s_s != '0' and s_s != p_s:
            return 'PASS', f'结构否决：singbet{s_s}与Pin{p_s}/365{b_s}相反'

    # R3: 价格否决 — 价格偏贵 + 市场方向弱（多数未追价）
    # 单纯偏贵不否决（可能仍有正EV），加上方向弱才否决
    if price_v == '偏贵' and dir_on_fav and (active <= 1 or qual == '偏弱'):
        return 'PASS', '弱方向+价格偏贵，无交易价值'

    return None


# ═══════════════════════════════════════════
#  ⑤ 庄家反证（Bookmaker Challenge）
#  4 个子检查，输出方向温度
# ═══════════════════════════════════════════

def _ln(bk, src):
    """读取某家公司 spread 线位（helper）"""
    sp = src.get(bk, {}).get('Spread', {})
    v = next(iter(sp.values()), {}) if len(sp) == 1 else {}
    return _fl(v.get('line'))

def bookmaker_challenge(mkt, dir_ah, active, price_v, euro_v):
    """
    BC1 — 三家盘口移动是否同步
    BC2 — 五家线位是否一致
    BC3 — 盘口移动与欧赔是否匹配
    BC4 — 是否存在合理反方解释
    返回 (temperature, details)
    """
    details = []

    # ── BC1: 线位同步 ──
    moves = {}
    for bk, label in [('Pinnacle', 'Pin'), ('Bet365', '365'), ('singbet', 'singbet')]:
        sl = _ln(bk, mkt.get('snap', {}))
        cl = _ln(bk, mkt.get('curr', {}))
        if sl is not None and cl is not None and round(cl - sl, 2) != 0:
            moves[label] = abs(cl - sl)
    if len(moves) >= 2:
        vals = list(moves.values())
        if max(vals) == min(vals):
            details.append('BC1 线位同步 Consensus（移动一致）')
        else:
            details.append('BC1 线位同步 Lead（移动幅度不同）')
    else:
        details.append('BC1 线位同步 Static（线位未动或仅一家移动）')

    # ── BC2: 线位一致性 ──
    lines = []
    for bk in BK_CORE:
        ln = _ln(bk, mkt.get('curr', {}))
        if ln is not None:
            lines.append(ln)
    if len(lines) >= 2:
        if len(set(round(l, 2) for l in lines)) == 1:
            details.append('BC2 市场成熟 Mature（全庄线位相同）')
        else:
            details.append('BC2 市场分散 Divergent（线位未统一）')

    # ── BC3: 盘口移动与欧赔 ──
    pin_snap = _ln('Pinnacle', mkt.get('snap', {}))
    pin_curr = _ln('Pinnacle', mkt.get('curr', {}))
    if pin_snap is not None and pin_curr is not None and round(pin_curr - pin_snap, 2) != 0:
        move = abs(pin_curr - pin_snap)
        fav = 'home' if dir_ah == '+' else 'away'
        sm = _gm('Pinnacle', mkt.get('snap', {}))
        cm = _gm('Pinnacle', mkt.get('curr', {}))
        tag = ''
        if sm and cm:
            so, co = _fl(sm.get(fav)), _fl(cm.get(fav))
            if so and co:
                tag = '，欧赔同步降' if co < so else '，欧赔反向涨' if co > so else ''
        details.append(f'BC3 盘口移动{move:.1f}格{tag}')
    else:
        details.append('BC3 盘口移动 Static')

    # ── BC4: 反证质问 ──
    reasons = []
    if active <= 1 and price_v == '偏贵':
        reasons.append('方向偏弱且价格偏贵')
    # 成熟市场：三家线位+方向侧水位全一致 → 无信息边际
    wkey = 'home' if dir_ah == '+' else 'away'
    r6_lines = []
    r6_waters = []
    for bk in BK_CORE:
        sp = mkt.get('curr', {}).get(bk, {}).get('Spread', {})
        sv = next(iter(sp.values()), {}) if len(sp) == 1 else {}
        ln = _fl(sv.get('line'))
        wt = _fl(sv.get(wkey))
        if ln is not None:
            r6_lines.append(ln)
        if wt is not None:
            r6_waters.append(wt)
    if len(r6_lines) == 3 and len(r6_waters) == 3:
        if max(r6_lines) - min(r6_lines) < 0.01 and max(r6_waters) - min(r6_waters) < 0.02:
            reasons.append('市场已完全定价（三家线位水位一致），无信息边际')
    if reasons:
        details.append(f'BC4 反证质问 Against（{"；".join(reasons)}）')
    else:
        details.append('BC4 反证质问 Support（无反方理由）')

    temperature = '关注' if reasons else '无异议'
    return temperature, ' | '.join(details)


# ═══════════════════════════════════════════
#  庄家平衡分析（Bookmaker Balance）
#  基于现有数据推断庄家最舒适/最怕的赛果
#  不参与决策，只输出叙事参考
# ═══════════════════════════════════════════

def bookmaker_balance(mkt, dir_ah, flipped, name):
    """庄家平衡：拆线位结构、庄家工具、赔付结构、最舒服结果"""
    def _line(bk, src):
        sp = src.get(bk, {}).get('Spread', {})
        v = next(iter(sp.values()), {}) if len(sp) == 1 else {}
        return _fl(v.get('line'))

    def _water(bk, src, side):
        sp = src.get(bk, {}).get('Spread', {})
        v = next(iter(sp.values()), {}) if len(sp) == 1 else {}
        return _fl(v.get(side))

    def _fmt(v):
        s = f'{abs(v):.2f}'.rstrip('0')
        return s[:-1] if s.endswith('.') else s

    home_name = name.split(' vs ')[0].strip()
    away_name = name.split(' vs ')[1].strip()
    team = home_name if dir_ah == '+' else away_name
    opp = away_name if dir_ah == '+' else home_name
    dir_label = f'主队{home_name}' if dir_ah == '+' else f'客队{away_name}'
    opp_label = f'客队{away_name}' if dir_ah == '+' else f'主队{home_name}'
    wkey = 'home' if dir_ah == '+' else 'away'

    # 推荐盘口 = 最深线位 + 对应水位
    bl = _pick_deepest_line(mkt['curr'], dir_ah)
    if bl is None:
        return '', []

    best_bk = None
    best_w = None
    for bk in BK_CORE:
        sp = mkt.get('curr', {}).get(bk, {}).get('Spread', {})
        v = next(iter(sp.values()), {}) if len(sp) == 1 else {}
        ln = _fl(v.get('line'))
        wt = _fl(v.get(wkey))
        if ln is not None and wt is not None and abs(ln - bl) < 0.01:
            best_bk = bk
            best_w = wt
            break

    dir_on_fav = (dir_ah == '+' and bl < 0) or (dir_ah == '-' and bl > 0)
    sign = '-' if dir_on_fav else '+'
    ah_str = f'{team}{sign}{_fmt(abs(bl))}'
    if best_w:
        ah_str += f' @{best_w:.2f}'
    items = []

    # ── 盘口拆解 ──
    ab = abs(bl)
    quarter = round(ab * 4) % 4
    if quarter == 0:
        split = f'{team}{sign}{_fmt(ab)} = 整数盘（走水可退注）'
    elif quarter == 2:
        split = f'{team}{sign}{_fmt(ab)} = 半球盘（无走水）'
    elif quarter == 1:
        if ab == 0.25:
            split = f'{team}{sign}{_fmt(ab)} = 半注{team}0 + 半注{team}{sign}0.5'
        else:
            split = f'{team}{sign}{_fmt(ab)} = 半注{team}{sign}{_fmt(ab-0.25)} + 半注{team}{sign}{_fmt(ab+0.25)}'
    else:  # quarter == 3
        split = f'{team}{sign}{_fmt(ab)} = 半注{team}{sign}{_fmt(ab-0.25)} + 半注{team}{sign}{_fmt(ab+0.25)}'
    items.append(split)

    # ── 关键赛果 ──
    scenarios = []
    if dir_on_fav:
        scenarios.append((f'{opp_label} 赢或平', -1))
        if quarter == 0:
            scenarios.append((f'{dir_label} 赢 {_fmt(ab)} 球 → 走水', int(ab)))
            scenarios.append((f'{dir_label} 赢 {_fmt(ab+1)} 球 → 全收', int(ab)+1))
        elif quarter == 2:
            scenarios.append((f'{dir_label} 赢 {_fmt(int(ab)+1)} 球 → 全收', int(ab)+1))
        elif quarter == 1:
            scenarios.append((f'平局 → {dir_label} 输半', 0))
            scenarios.append((f'{dir_label} 赢 {_fmt(int(ab)+1)} 球 → 全收', int(ab)+1))
        else:  # quarter == 3
            scenarios.append((f'{dir_label} 赢 {_fmt(int(ab)+1)} 球 → 赢半', int(ab)+1))
            scenarios.append((f'{dir_label} 赢 {_fmt(int(ab)+2)} 球 → 全收', int(ab)+2))
    else:
        scenarios.append((f'{dir_label} 赢或平 → 全收', 1))
        if quarter == 0:
            scenarios.append((f'{dir_label} 输 {_fmt(ab)} 球 → 走水', -int(ab)))
            scenarios.append((f'{dir_label} 输 {_fmt(ab+1)} 球 → 全输', -(int(ab)+1)))
        elif quarter == 2:
            scenarios.append((f'{dir_label} 输 {_fmt(int(ab)+1)} 球 → 全输', -(int(ab)+1)))
        elif quarter == 1:
            scenarios.append((f'{dir_label} 输 {_fmt(int(ab)+1)} 球 → 全输', -(int(ab)+1)))
        else:  # quarter == 3
            scenarios.append((f'{dir_label} 输 {_fmt(int(ab)+1)} 球 → 输半', -(int(ab)+1)))
            scenarios.append((f'{dir_label} 输 {_fmt(int(ab)+2)} 球 → 全输', -(int(ab)+2)))

    for label, _ in scenarios:
        items.append(f'关键赛果：{label}')

    # 更丰富的赔付场景（比分更多）
    if dir_on_fav:
        payout_margins = list(range(-1, int(ab) + 3))  # -1, 0, 1, ..., ab+2
    else:
        payout_margins = list(range(-(int(ab) + 2), 2))  # -(ab+2), ..., -1, 0, 1

    payout_scenarios = []
    for m in payout_margins:
        if m == -1:
            label = f'{opp_label} 赢或平'
        elif m == 0:
            label = '平局'
        elif m == 1:
            label = f'{dir_label} 赢或平' if not dir_on_fav else f'{dir_label} 赢 1 球'
        elif m > 1:
            label = f'{dir_label} 赢 {m} 球'
        else:
            label = f'{dir_label} 输 {abs(m)} 球'
        payout_scenarios.append((label, m))

    # ── 庄家工具 ──
    tools = []

    # 线位变动
    line_moves = []
    for bk, label in [('Pinnacle', 'Pin'), ('Bet365', '365'), ('singbet', 'Crown')]:
        sl = _line(bk, mkt.get('snap', {}))
        cl = _line(bk, mkt.get('curr', {}))
        if sl is not None and cl is not None:
            move = round(abs(cl) - abs(sl), 2)
            if abs(move) >= 0.25:
                direction = '推盘' if move > 0 else '退盘'
                line_moves.append(f'{label}{direction}{_fmt(abs(move))}({_fmt(sl)}→{_fmt(cl)})')
    if line_moves:
        tools.append(' | '.join(line_moves))
    else:
        tools.append('线位未动')

    # 水位变动
    water_moves = []
    for bk, label in [('Pinnacle', 'Pin'), ('Bet365', '365'), ('singbet', 'Crown')]:
        sw = _water(bk, mkt.get('snap', {}), wkey)
        cw = _water(bk, mkt.get('curr', {}), wkey)
        if sw and cw and abs(cw - sw) >= 0.02:
            direction = '升' if cw > sw else '降'
            water_moves.append(f'{label}{direction}{abs(cw-sw):.2f}({sw:.2f}→{cw:.2f})')
    if water_moves:
        tools.append(' | '.join(water_moves))

    # 欧赔变动
    euro_moves = []
    for bk in BK_EURO:
        s_ml = _gm(bk, mkt.get('snap', {}))
        c_ml = _gm(bk, mkt.get('curr', {}))
        if not s_ml or not c_ml:
            continue
        snap_o = _fl(s_ml.get(wkey))
        curr_o = _fl(c_ml.get(wkey))
        if snap_o and curr_o:
            chg = (snap_o - curr_o) / snap_o
            if abs(chg) >= 0.02:
                direction = '降' if chg > 0 else '涨'
                euro_moves.append(f'{BK_LABEL.get(bk,bk)}{direction}{abs(chg)*100:.0f}%')
    if euro_moves:
        tools.append(' | '.join(euro_moves))

    # 平赔变动
    draw_moves = []
    for bk in BK_EURO:
        s_ml = _gm(bk, mkt.get('snap', {}))
        c_ml = _gm(bk, mkt.get('curr', {}))
        if not s_ml or not c_ml:
            continue
        ds = _fl(s_ml.get('draw'))
        dc = _fl(c_ml.get('draw'))
        if ds and dc:
            dchg = (ds - dc) / ds
            if abs(dchg) >= 0.03:
                direction = '降' if dchg > 0 else '涨'
                draw_moves.append(f'{BK_LABEL.get(bk,bk)}{direction}{abs(dchg)*100:.0f}%')
    if draw_moves:
        tools.append(' | '.join(draw_moves))

    if tools and not (len(tools) == 1 and tools[0].startswith('线位未动')):
        items.append('庄家工具：')
        for t in tools:
            items.append(f'  - {t}')

    # ── 大小球 ──
    ou_summary = []
    for bk, label in [('Pinnacle', 'Pin'), ('Bet365', '365'), ('singbet', 'Crown')]:
        sc = next(iter(mkt.get('snap', {}).get(bk, {}).get('Totals', {}).values()), None)
        cc = next(iter(mkt.get('curr', {}).get(bk, {}).get('Totals', {}).values()), None)
        if sc and cc:
            sl = _fl(sc.get('line'))
            cl = _fl(cc.get('line'))
            so = _fl(sc.get('home'))
            co = _fl(cc.get('home'))
            if sl is not None and cl is not None and so is not None and co is not None:
                line_chg = '升盘' if cl > sl else '退盘' if cl < sl else '不动'
                water_chg = f"{'涨' if co > so else '降'}{abs(co-so):.2f}" if abs(co-so)>=0.02 else '水不动'
                ou_summary.append(f'{label} {_fmt(sl)}→{_fmt(cl)}({line_chg},{water_chg})')
    if ou_summary:
        items.append('大小球：' + ' | '.join(ou_summary))

    # ── 赔付结构（纯文本） ──
    ou_line = ou_over_w = ou_under_w = None
    for bk in ['Pinnacle', 'Bet365', 'singbet']:
        cc = next(iter(mkt.get('curr', {}).get(bk, {}).get('Totals', {}).values()), None)
        if cc:
            ou_line = _fl(cc.get('line'))
            ou_over_w = _fl(cc.get('home'))
            ou_under_w = _fl(cc.get('under'))
            if ou_line is not None:
                break

    if ou_line is not None:
        items.append(f'亚盘：{team}{sign}{_fmt(ab)} @ {best_w:.2f}' if best_w else f'亚盘：{team}{sign}{_fmt(ab)}')
        if ou_over_w and ou_under_w:
            items.append(f'大小球：{_fmt(ou_line)}（大球 {ou_over_w:.2f} / 小球 {ou_under_w:.2f}）')

        def make_score(margin):
            margin = int(margin)
            if margin > 0:
                return (margin + 1, 1) if dir_ah == '+' else (1, margin + 1)
            elif margin < 0:
                return (1, abs(margin) + 1) if dir_ah == '+' else (abs(margin) + 1, 1)
            else:
                return (1, 1)

        def ah_result(score):
            hg, ag = score
            net = hg - ag + bl if dir_ah == '+' else ag - hg - bl
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

        def ou_result(score):
            total = score[0] + score[1]
            if total > ou_line + 0.01:
                return '大球'
            elif total < ou_line - 0.01:
                return '小球'
            else:
                return '走水'

        def settle(ah, ou):
            if ah == '全收':
                return '庄家大赔' if ou == '大球' else '庄家中赔'
            elif ah == '赢半':
                return '庄家小赔'
            elif ah == '走水':
                return '庄家平衡' if ou in ('小球', '走水') else '庄家小赔'
            elif ah == '输半':
                return '庄家小赚' if ou == '小球' else '庄家小赔'
            else:
                return '庄家大赚' if ou in ('小球', '走水', '—') else '庄家小赚'

        items.append('赔付结构：')
        for label, margin in payout_scenarios:
            score = make_score(margin)
            ah = ah_result(score)
            ou = ou_result(score)
            st = settle(ah, ou)
            items.append(f'  - {label} 比分 {score[0]}-{score[1]} → 亚盘{ah}，大小球{ou}，{st}')

        # ── 庄家最舒服比分 ──
        if dir_on_fav:
            comfort_margin = -1 if quarter == 0 else 0
        else:
            if quarter == 0:
                comfort_margin = -(int(ab)+1)  # 整数盘多输一球 → 全输
            elif quarter == 3:
                comfort_margin = -(int(ab)+2)  # 0.75盘输穿盘线+1 → 全输
            else:
                comfort_margin = -1  # 半球/0.25盘输1球 → 全输
        score = make_score(comfort_margin)
        total = score[0] + score[1]
        ou_side = '大球' if total > ou_line else '小球' if total < ou_line else '走水'
        ah = ah_result(score)
        st = settle(ah, ou_side)
        items.append(f'庄家最舒服比分：{score[0]}-{score[1]}（{st}，{ou_side}）')

    return f'{ah_str}（{BK_LABEL.get(best_bk, "Pin")}）', items


# ═══════════════════════════════════════════
#  ③ 交易员决策层（三明治法第三层）
#  严格按 TRADING_FLOW.md 框架输出，无框架不输出
# ═══════════════════════════════════════════
#  ③ 盘口交易员视角（强制框架）
#  最终目标：回答四个问题，最后给出庄家视角的交易建议。
#  约束：不预测比赛结果，只预测盘口行为；证据不足则 PASS。
# ═══════════════════════════════════════════

def _fmt_line(v, dir_ah):
    """从方向方视角格式化盘口线位"""
    if v is None:
        return ''
    # 方向方视角：让球方用 '-'，受让方用 '+'
    if dir_ah == '+':
        sign = '-' if v < 0 else '+'
    else:
        sign = '-' if v > 0 else '+'
    s = f'{abs(v):.2f}'.rstrip('0')
    s = s[:-1] if s.endswith('.') else s
    return f'{sign}{s}'


def _fmt(v):
    s = f'{abs(v):.2f}'.rstrip('0')
    return s[:-1] if s.endswith('.') else s


def _describe_bookmaker_action(mkt, dir_ah):
    """Q1: 庄家现在在干什么？按动作类型汇总，避免冗长。"""
    wkey = 'home' if dir_ah == '+' else 'away'
    parts = []

    # 亚盘线位
    line_push = line_drop = 0
    max_push = max_drop = 0.0
    for bk in BK_CORE:
        sp = mkt.get('snap', {}).get(bk, {}).get('Spread', {})
        cv = mkt.get('curr', {}).get(bk, {}).get('Spread', {})
        sv = next(iter(sp.values()), {}) if len(sp) == 1 else {}
        cv_v = next(iter(cv.values()), {}) if len(cv) == 1 else {}
        sl = _fl(sv.get('line'))
        cl = _fl(cv_v.get('line'))
        if sl is not None and cl is not None:
            diff = round(abs(cl) - abs(sl), 2)
            if diff >= 0.25:
                line_push += 1
                max_push = max(max_push, diff)
            elif diff <= -0.25:
                line_drop += 1
                max_drop = max(max_drop, abs(diff))
    if line_push and line_drop:
        parts.append(f'亚盘分歧（{line_push}家推盘{_fmt(max_push)}、{line_drop}家退盘{_fmt(max_drop)}）')
    elif line_push:
        parts.append(f'亚盘推盘{_fmt(max_push)}（{line_push}家）')
    elif line_drop:
        parts.append(f'亚盘退盘{_fmt(max_drop)}（{line_drop}家）')

    # 亚盘水位（方向方）
    water_down = water_up = 0
    max_wd = max_wu = 0.0
    for bk in BK_CORE:
        sp = mkt.get('snap', {}).get(bk, {}).get('Spread', {})
        cv = mkt.get('curr', {}).get(bk, {}).get('Spread', {})
        sv = next(iter(sp.values()), {}) if len(sp) == 1 else {}
        cv_v = next(iter(cv.values()), {}) if len(cv) == 1 else {}
        sw = _fl(sv.get(wkey))
        cw = _fl(cv_v.get(wkey))
        if sw and cw:
            diff = round(cw - sw, 2)
            if diff <= -0.03:
                water_down += 1
                max_wd = max(max_wd, abs(diff))
            elif diff >= 0.03:
                water_up += 1
                max_wu = max(max_wu, diff)
    if water_down and water_up:
        parts.append(f'方向方水位分化（{water_down}家降水、{water_up}家升水）')
    elif water_down:
        parts.append(f'方向方降水{_fmt(max_wd)}（{water_down}家）')
    elif water_up:
        parts.append(f'方向方升水{_fmt(max_wu)}（{water_up}家）')

    # 欧赔
    euro_down = euro_up = 0
    for bk in BK_EURO:
        s_ml = _gm(bk, mkt.get('snap', {}))
        c_ml = _gm(bk, mkt.get('curr', {}))
        if s_ml and c_ml:
            snap_o = _fl(s_ml.get(wkey))
            curr_o = _fl(c_ml.get(wkey))
            if snap_o and curr_o:
                chg = (snap_o - curr_o) / snap_o
                if chg >= 0.02:
                    euro_down += 1
                elif chg <= -0.02:
                    euro_up += 1
    if euro_down and euro_up:
        parts.append('欧赔方向方分化')
    elif euro_down:
        parts.append(f'欧赔方向方降赔（{euro_down}家）')
    elif euro_up:
        parts.append(f'欧赔方向方涨赔（{euro_up}家）')

    # 平赔
    draw_up = draw_down = 0
    for bk in BK_EURO:
        s_ml = _gm(bk, mkt.get('snap', {}))
        c_ml = _gm(bk, mkt.get('curr', {}))
        if s_ml and c_ml:
            ds = _fl(s_ml.get('draw'))
            dc = _fl(c_ml.get('draw'))
            if ds and dc:
                if dc > ds * 1.03:
                    draw_up += 1
                elif dc < ds * 0.97:
                    draw_down += 1
    if draw_up and draw_down:
        parts.append('平赔分化')
    elif draw_up >= 2:
        parts.append(f'平赔涨（{draw_up}家）')
    elif draw_down >= 2:
        parts.append(f'平赔降（{draw_down}家）')
    elif draw_up:
        parts.append('平赔涨')
    elif draw_down:
        parts.append('平赔降')

    # 大小球
    ou_push = ou_drop = 0
    ou_water_up = ou_water_down = 0
    for bk in BK_CORE:
        sc = next(iter(mkt.get('snap', {}).get(bk, {}).get('Totals', {}).values()), None)
        cc = next(iter(mkt.get('curr', {}).get(bk, {}).get('Totals', {}).values()), None)
        if sc and cc:
            sl = _fl(sc.get('line'))
            cl = _fl(cc.get('line'))
            so = _fl(sc.get('home'))
            co = _fl(cc.get('home'))
            if sl is not None and cl is not None:
                if cl > sl:
                    ou_push += 1
                elif cl < sl:
                    ou_drop += 1
            if so and co:
                diff = round(co - so, 2)
                if diff >= 0.03:
                    ou_water_up += 1
                elif diff <= -0.03:
                    ou_water_down += 1
    ou_parts = []
    if ou_push and ou_drop:
        ou_parts.append('大小球升降盘分歧')
    elif ou_push:
        ou_parts.append(f'大小球升盘（{ou_push}家）')
    elif ou_drop:
        ou_parts.append(f'大小球退盘（{ou_drop}家）')
    if ou_water_up and ou_water_down:
        ou_parts.append('大球水位分化')
    elif ou_water_up:
        ou_parts.append(f'大球升水（{ou_water_up}家）')
    elif ou_water_down:
        ou_parts.append(f'大球降水（{ou_water_down}家）')
    if ou_parts:
        parts.append('、'.join(ou_parts))

    if not parts:
        return '盘口整体未动，庄家没有明显动作'
    return '；'.join(parts)


def _explain_pricing(q1, mkt, dir_ah, price_v):
    """Q2: 庄家为什么这样定价？"""
    bl = _pick_deepest_line(mkt['curr'], dir_ah)
    dir_on_fav = False
    if bl is not None:
        dir_on_fav = (dir_ah == '+' and bl < 0) or (dir_ah == '-' and bl > 0)
    ou_dir, ou_desc = check_ou(mkt)

    if '诱盘' in (ou_desc or ''):
        return f'大小球{ou_dir}出现价量背离，庄家在诱{ou_dir}，实际方向应与{ou_dir}相反'
    if '平赔骤降' in q1 or '平赔降' in q1:
        side = '让球方' if dir_on_fav else '受让方'
        return f'平赔下降说明庄家在承接平局资金，{side}穿盘风险被低估'
    if '亚盘推盘' in q1 and '方向方降水' in q1 and '欧赔方向方降赔' in q1:
        return '亚盘推盘+方向方降水+欧赔降赔三线同步，庄家真看好方向方'
    if '亚盘推盘' in q1 and '方向方升水' in q1:
        return '亚盘推盘但方向方水位上升，庄家让利吸筹，更可能是平衡资金而非真看好'
    if '亚盘退盘' in q1 and '方向方降水' in q1:
        return '亚盘退盘+方向方降水，庄家主动降低门槛并降价，诱导资金进入方向方'
    if '平赔涨' in q1 and '欧赔方向方降赔' in q1:
        return '平赔涨+方向方赔率降，庄家不愿接平局资金，方向方赢面被市场认可'
    if '盘口整体未动' in q1:
        return '盘口未动，庄家对当前定价满意，没有明显引导资金的意图'
    return '庄家动作相互抵消，定价意图不明确'


def _bookmaker_risk(mkt, dir_ah, q1, q2):
    """Q3: 庄家现在承担哪边风险？"""
    bl = _pick_deepest_line(mkt['curr'], dir_ah)
    dir_on_fav = False
    if bl is not None:
        dir_on_fav = (dir_ah == '+' and bl < 0) or (dir_ah == '-' and bl > 0)
    side = '让球方' if dir_on_fav else '受让方'
    opp_side = '受让方' if dir_on_fav else '让球方'

    if '方向方降水' in q1 or '欧赔方向方降赔' in q1 or '亚盘推盘' in q1:
        return f'庄家在接{side}资金，当前主要承担{side}赢盘的风险'
    if '方向方升水' in q1 or '欧赔方向方涨赔' in q1 or '亚盘退盘' in q1:
        return f'庄家在让利{side}，实际更担心{opp_side}打出'
    if '诱' in q2:
        return f'庄家在诱散户去错误方向，真实风险在{opp_side}'
    return '庄家没有明显单边敞口，当前风险较平衡'


def _comfortable_outcome(mkt, dir_ah, name):
    """Q4: 哪个结果最符合庄家的赔付利益？"""
    bl = _pick_deepest_line(mkt['curr'], dir_ah)
    if bl is None:
        return None, None
    ab = abs(bl)
    quarter = round(ab * 4) % 4
    dir_on_fav = (dir_ah == '+' and bl < 0) or (dir_ah == '-' and bl > 0)

    ou_line = None
    for bk in ['Pinnacle', 'Bet365', 'singbet']:
        cc = next(iter(mkt.get('curr', {}).get(bk, {}).get('Totals', {}).values()), None)
        if cc:
            ou_line = _fl(cc.get('line'))
            if ou_line is not None:
                break

    if dir_on_fav:
        margin = -1 if quarter == 0 else 0
    else:
        if quarter == 0:
            margin = -(int(ab)+1)
        elif quarter == 3:
            margin = -(int(ab)+2)
        else:
            margin = -1

    margin = int(margin)
    if margin > 0:
        score = (margin + 1, 1) if dir_ah == '+' else (1, margin + 1)
    elif margin < 0:
        score = (1, abs(margin) + 1) if dir_ah == '+' else (abs(margin) + 1, 1)
    else:
        score = (1, 1)

    if ou_line is not None:
        total = score[0] + score[1]
        ou_side = '大球' if total > ou_line else '小球' if total < ou_line else '走水'
    else:
        ou_side = '—'

    return score, ou_side


def evaluate_fit_and_flaw(mkt, dir_ah):
    """第二层：庄家平衡 —— 合拍度 + 破绽"""
    bl = _pick_deepest_line(mkt['curr'], dir_ah)
    dir_on_fav = False
    if bl is not None:
        dir_on_fav = (dir_ah == '+' and bl < 0) or (dir_ah == '-' and bl > 0)

    # ── 合拍度 ──
    draw_up = draw_down = 0
    for bk in BK_EURO:
        s_ml = _gm(bk, mkt.get('snap', {}))
        c_ml = _gm(bk, mkt.get('curr', {}))
        if s_ml and c_ml:
            ds = _fl(s_ml.get('draw'))
            dc = _fl(c_ml.get('draw'))
            if ds and dc:
                if dc > ds * 1.03:
                    draw_up += 1
                elif dc < ds * 0.97:
                    draw_down += 1

    ou_dir, ou_desc = check_ou(mkt)
    ou_is_trap = ou_desc and '诱盘' in ou_desc
    ou_strong = ou_dir in ('大球', '小球') and not ou_is_trap

    score = 0
    fit_reasons = []

    if draw_up >= 2:
        score += 1
        fit_reasons.append('平赔涨')
    if draw_down >= 3:
        score -= 2
        fit_reasons.append('平赔骤降')
    elif draw_down >= 2:
        score -= 1
        fit_reasons.append('平赔跌')

    if ou_dir:
        if dir_on_fav and ou_dir == '大球' and ou_strong:
            score += 1
            fit_reasons.append('AH/OU同向')
        elif dir_on_fav and ou_dir == '小球' and ou_strong:
            score -= 1
            fit_reasons.append('AH/OU矛盾')
        elif not dir_on_fav and ou_dir == '小球' and ou_strong:
            score += 1
            fit_reasons.append('AH/OU同向')
        elif not dir_on_fav and ou_dir == '大球' and ou_strong:
            score -= 1
            fit_reasons.append('AH/OU矛盾')

    if score >= 1:
        fit = '合拍'
    elif score <= -1:
        fit = '矛盾'
    else:
        fit = '中性'

    # ── 破绽 ──
    flaws = []
    if ou_is_trap:
        if '大球水升' in (ou_desc or ''):
            trap_label = '诱大球'
        elif '小球水升' in (ou_desc or ''):
            trap_label = '诱小球'
        else:
            trap_label = f'{ou_dir}(诱盘)'
        flaws.append(trap_label)

    # 亚盘滞后定价
    lines = {}
    for bk in BK_CORE:
        ln = _ln(bk, mkt.get('curr', {}))
        if ln is not None:
            lines[bk] = ln
    if len(lines) >= 2:
        vals = sorted(lines.values())
        median = vals[len(vals) // 2]
        for bk, ln in lines.items():
            if abs(ln - median) >= 0.5:
                flaws.append(f'{BK_LABEL[bk]}滞后定价')
                break

    # 大小球跨庄差≥0.5
    ou_lines = {}
    for bk in BK_CORE:
        cc = next(iter(mkt.get('curr', {}).get(bk, {}).get('Totals', {}).values()), None)
        if cc:
            ln = _fl(cc.get('line'))
            if ln is not None:
                ou_lines[bk] = ln
    if len(ou_lines) >= 2:
        vals = list(ou_lines.values())
        if max(vals) - min(vals) >= 0.5:
            flaws.append('O/U跨庄差≥0.5')

    return {
        'fit': fit,
        'fit_reasons': fit_reasons,
        'flaw': len(flaws) > 0,
        'flaws': flaws,
    }


def trader_analysis(mkt, dir_ah, system_conc, price_v, name):
    """
    第三层：盘口交易员视角。
    强制回答四个问题，最后给出庄家视角的交易建议。
    证据不足 → 推荐 PASS。
    """
    home_name = name.split(' vs ')[0].strip()
    away_name = name.split(' vs ')[1].strip()
    team = home_name if dir_ah == '+' else away_name
    opp = away_name if dir_ah == '+' else home_name
    wkey = 'home' if dir_ah == '+' else 'away'

    bl = _pick_deepest_line(mkt['curr'], dir_ah)
    dir_on_fav = False
    if bl is not None:
        dir_on_fav = (dir_ah == '+' and bl < 0) or (dir_ah == '-' and bl > 0)

    # Q1-Q4
    q1 = _describe_bookmaker_action(mkt, dir_ah)
    # 引入第二层：合拍度 + 破绽
    assess = evaluate_fit_and_flaw(mkt, dir_ah)
    q2 = _explain_pricing(q1, mkt, dir_ah, price_v)
    q3 = _bookmaker_risk(mkt, dir_ah, q1, q2)
    comfort_score, comfort_ou = _comfortable_outcome(mkt, dir_ah, name)
    q4 = f'{comfort_score[0]}-{comfort_score[1]}（AH收方向注，{comfort_ou}）' if comfort_score else '无法判断'

    # 证据强度检查
    ou_dir, ou_desc = check_ou(mkt)
    has_clear_signal = ('推盘' in q1 or '退盘' in q1 or '降水' in q1 or '升水' in q1 or
                        '降赔' in q1 or '涨赔' in q1 or '平赔' in q1 or
                        (ou_dir in ('大球', '小球')))

    # 默认：证据不足 PASS
    trade_side = 'PASS'
    product = '-'
    size = '0%'
    reason = '盘口证据不足，禁止猜测'

    if not has_clear_signal:
        trade_side = 'PASS'
        reason = '盘口无明确动作，证据不足，PASS'
    else:
        ou_line = ou_over = ou_under = ou_bk = None
        for bk in ['Pinnacle', 'Bet365', 'singbet']:
            cc = next(iter(mkt.get('curr', {}).get(bk, {}).get('Totals', {}).values()), None)
            if cc:
                ou_line = _fl(cc.get('line'))
                ou_over = _fl(cc.get('home'))
                ou_under = _fl(cc.get('under'))
                ou_bk = BK_LABEL.get(bk, bk)
                if ou_line is not None:
                    break
        ou_order = '大' if ou_dir == '大球' else '小'
        ou_water = ou_over if ou_dir == '大球' else ou_under
        ou_prod = f'{ou_order}{_fmt(ou_line)} @{ou_water:.2f} ({ou_bk})' if ou_water and ou_line else '-'
        ou_has_signal = ou_dir in ('大球', '小球')

        # ── 先确定 AH 方向 ──
        best_bk = None
        best_w = None
        for bk in BK_CORE:
            sp = mkt.get('curr', {}).get(bk, {}).get('Spread', {})
            v = next(iter(sp.values()), {}) if len(sp) == 1 else {}
            ln = _fl(v.get('line'))
            wt = _fl(v.get(wkey))
            if ln is not None and wt is not None and abs(ln - bl) < 0.01:
                best_bk = bk
                best_w = wt
                break

        ah_side = None
        ah_prod = '-'
        ah_reason = ''

        if '推盘' in q1 and '降水' in q1 and '降赔' in q1 and price_v != '偏贵':
            ah_prod = f'{team}{_fmt_line(bl, dir_ah)} @{best_w:.2f} ({BK_LABEL.get(best_bk,"Pin")})'
            ah_side = team
            ah_reason = '三线同步，跟方向方'
        elif '推盘' in q1 and '升水' in q1:
            ah_prod = f'{opp}{_fmt_line(bl, "+" if dir_ah == "-" else "-") if bl else ""}'
            ah_side = opp
            ah_reason = '推盘+升水让利，诱方向方，反打对方'
        elif '平赔降' in q1 or '平赔骤降' in q1:
            # 平赔降：方向方风险上升
            ah_prod = f'{opp}{_fmt_line(bl, "+" if dir_ah == "-" else "-") if bl else ""}'
            ah_side = opp
            ah_reason = '平赔降，方向方风险'
        elif price_v == '合理' or price_v == '偏便宜':
            ah_prod = f'{team}{_fmt_line(bl, dir_ah)} @{best_w:.2f} ({BK_LABEL.get(best_bk,"Pin")})'
            ah_side = team
            ah_reason = '价格合理，跟方向方'

        # ── 融合 AH + OU ──
        if ah_side and ou_has_signal:
            ou_label = '大球' if ou_dir == '大球' else '小球'
            if '诱盘' in (ou_desc or ''):
                # 从描述中提取诱盘方向
                if '大球水升' in ou_desc:
                    ou_reason = f'少数认为诱大球，多数看{ou_label}'
                elif '小球水升' in ou_desc:
                    ou_reason = f'少数认为诱小球，多数看{ou_label}'
                else:
                    ou_reason = f'存在分歧，多数看{ou_label}'
            else:
                ou_reason = f'真{ou_label}信号'
            product = f'{ah_prod} + {ou_prod}'
            size = 'AH 10-15%, OU 5-10%'
            trade_side = f'{ah_side} + {ou_label}'
            reason = f'{ah_reason}；{ou_reason}'
        elif ah_side:
            product = ah_prod
            size = '10-15%'
            trade_side = ah_side
            reason = ah_reason
        elif ou_has_signal:
            ou_label = '大球' if ou_dir == '大球' else '小球'
            if '诱盘' in (ou_desc or ''):
                if '大球水升' in ou_desc:
                    ou_reason = f'少数认为诱大球，多数看{ou_label}'
                elif '小球水升' in ou_desc:
                    ou_reason = f'少数认为诱小球，多数看{ou_label}'
                else:
                    ou_reason = f'存在分歧，多数看{ou_label}'
            else:
                ou_reason = f'真{ou_label}信号'
            product = ou_prod
            size = '10-15%'
            trade_side = ou_dir
            reason = ou_reason
        else:
            trade_side = 'PASS'
            reason = '盘口证据不足以支撑明确交易，PASS'

    # 追加合拍度/破绽到原因（来自第二层）
    fit_str = assess.get('fit', '')
    flaw_str = '；'.join(assess.get('flaws', [])) if assess.get('flaw') else ''
    if fit_str and reason and reason != '盘口证据不足，禁止猜测':
        reason += f'（合拍度：{fit_str}'
        if flaw_str:
            reason += f'，破绽：{flaw_str}'
        reason += '）'

    return {
        'q1': q1,
        'q2': q2,
        'q3': q3,
        'q4': q4,
        'trade_side': trade_side,
        'product': product,
        'size': size,
        'reason': reason,
    }


def analyze(name, mkt):


    result = {'name': name}
    info = []  # 信息项，不影响结论
    verdicts = []  # 各步判定摘要

    # 大小球（先算，所有路径都能展示）
    ou_dir, ou_desc = check_ou(mkt)
    result['大小球'] = f'{ou_dir} {ou_desc}' if ou_dir else None

    # ── ① 找方向 ──
    dir_ah, qual, signals, dissent_bk, active, dir_mode = get_direction(mkt)
    if dir_ah is None:
        sigs = ' '.join(f'{BK_LABEL[b]}{signals[b]}' for b in BK_CORE)
        result['信号'] = sigs
        return _fin(result, 'PASS', f'无方向（{sigs}）', info)

    result['方向'] = dir_ah
    result['方向模式'] = dir_mode
    sig_core = ' '.join(f'{BK_LABEL[b]}{signals[b]}' for b in BK_CORE)
    result['信号'] = sig_core
    result['_bl'] = _pick_deepest_line(mkt['curr'], dir_ah)

    if dir_mode == 'static':
        info.append(f'静态位置：{sig_core}')
    elif active == 3:
        info.append(f'方向一致：{sig_core}')
        verdicts.append('方向明确')
    elif active == 2 and dissent_bk:
        d = BK_LABEL.get(dissent_bk, dissent_bk) or '?'
        info.append(f'方向成立（{d}分歧）：{sig_core}')
        verdicts.append('方向有分歧')
    elif active == 2:
        info.append(f'两家一致：{sig_core}')
        verdicts.append('方向明确')
    else:
        voter = next((BK_LABEL[b] for b in BK_CORE if signals[b] != '0'), '?')
        info.append(f'单家指向（{voter}）：{sig_core}')
        verdicts.append('方向偏弱')

    # ── 平赔预警（信息，先算，过路时带上） ──
    dd = check_draw_drop(mkt)
    if dd:
        info.append(dd[0])

    # ── ② 共识验证（Consensus）──
    # 收敛度
    conv, snap_rg, curr_rg, bk_lines = check_convergence(mkt)
    if conv is not None:
        if conv >= 0.25:
            info.append(f'明显收敛（初盘极差{snap_rg}→现盘极差{curr_rg}，收敛{conv}格）')
            verdicts.append('市场收敛')
        elif conv < 0:
            info.append(f'⚠️分歧扩大（初盘极差{snap_rg}→现盘极差{curr_rg}，发散{abs(conv)}格）')
            verdicts.append('分歧扩大')
        else:
            info.append(f'轻度收敛（初盘极差{snap_rg}→现盘极差{curr_rg}）')
            verdicts.append('轻度收敛')

    # 平局翻转
    draw_f, draw_src, draw_type = check_draw_signal(mkt, dir_ah)
    flipped = False
    if draw_f:
        # 判断方向方是让球方还是受让方
        # 线位<0=主队让球(主队是让球方) 线位>0=客队让球(客队是让球方)
        # 只有方向方=让球方时才翻转，否则保持方向
        ref_line = None
        for bk in BK_CORE:
            ln = _fl(_gs(bk, mkt['curr']).get('line'))
            if ln is not None:
                ref_line = ln
                break
        can_flip = False
        if ref_line is not None and ref_line != 0:
            if dir_ah == '+' and ref_line < 0:
                can_flip = True  # 方向主队 + 主队让球 = 让球方
            elif dir_ah == '-' and ref_line > 0:
                can_flip = True  # 方向客队 + 客队让球 = 让球方

        src_name = '平赔骤降' if draw_type == 'B' else (BK_FULL.get(draw_src, draw_src) if draw_src else '?')
        if can_flip:
            flipped = True
            dir_ah = '-' if dir_ah == '+' else '+'
            result['方向'] = dir_ah
            info.append(f'平局信号（{src_name}），从让球方翻转到受让方')
            verdicts.append('平局翻转')
            result['_bl'] = _pick_deepest_line(mkt['curr'], dir_ah)
            result['翻转类型'] = '让球方→受让方'
        else:
            flipped = True  # 标记已处理平局信号，避免被共识否决提前 return
            result['翻转类型'] = '方向已在受让方（平局信号触发）'
            info.append(f'平局信号（{src_name}），方向已在受让方，无需翻转')
            verdicts.append('平局信号（无需翻转）')

    # 欧赔检查
    euro_v, euro_d = check_euro(mkt, dir_ah)
    euro_str = ' '.join(f'{BK_LABEL[b]}{v[0]}' for b, v in
                        sorted(euro_d.items(), key=lambda x: BK_EURO.index(x[0])))
    result['欧赔'] = euro_v
    result['欧赔明细'] = euro_str
    if euro_v == '支持':
        verdicts.append('欧赔支持')
    else:
        verdicts.append('欧赔中性')

    # ── ③ 价格发现（Price Discovery）──
    price_v, price_d = check_price(mkt, dir_ah, None)
    price_label = '强化' if price_v == '偏便宜' else '确认' if price_v == '合理' else '存疑'
    result['价格'] = price_v
    result['价格结论'] = price_label
    info.append(f'{price_label}（{price_d}）')
    verdicts.append(f'价格{price_label}')

    # ── 盘口管理分析（提前计算，确保所有路径都能输出）──
    if dir_ah is not None:
        best_line, bal_items = bookmaker_balance(mkt, dir_ah, flipped, name)
        result['盘口管理'] = bal_items
        result['_best_line'] = best_line

    # ── 庄家反证（Bookmaker Challenge，提前计算）──
    bc_level, bc_reason = bookmaker_challenge(mkt, dir_ah, active, price_v, euro_v)
    result['BC'] = bc_level
    result['BC理由'] = bc_reason
    if bc_level == '过热':
        result['BC判决'] = '过热'
        info.append(f'方向过热：{bc_reason}')
    elif bc_level == '关注':
        result['BC判决'] = '关注'
        info.append(f'BC关注：{bc_reason}')
    else:
        result['BC判决'] = '无异议'

    # 共识否决 → PASS（翻转后欧赔反对可接受，但放在价格/BC/盘口之后确保完整输出）
    if euro_v == '反对' and not flipped:
        return _fin(result, 'PASS', f'共识否决：欧赔反对（{euro_str}）', info)

    # ── ④ 风控否决（Risk Filter，含R4/R5结构否决）──
    risk = check_risk_filter(mkt, dir_ah, active, qual, price_v, euro_v, flipped, signals)
    if risk:
        risk_conc, risk_reason = risk
        info.append(risk_reason)
        return _fin(result, risk_conc, risk_reason, info, flipped)

    # ── 合拍度 + 破绽（系统结论的补充维度）──
    _assess = evaluate_fit_and_flaw(mkt, dir_ah)
    result['合拍度'] = _assess['fit']
    result['合拍度理由'] = _assess['fit_reasons']
    result['破绽'] = _assess['flaws']

    # ── ⑥ 最终决策（Final Decision）──
    # 第一层：系统结论快速分类
    if flipped:
        if euro_v == '反对':
            system_conc = 'PASS'
            system_reason = '翻转后欧赔仍反对'
        else:
            system_conc = 'WATCHLIST'
            system_reason = '翻转·价格确认'
    elif euro_v == '反对':
        system_conc = 'PASS'
        system_reason = '共识否决：欧赔反对'
    elif euro_v == '中性':
        system_conc = 'WATCHLIST'
        system_reason = '共识中立'
    elif qual == '偏弱':
        system_conc = 'WATCHLIST'
        system_reason = '方向偏弱：仅单家变盘'
    elif bc_level in ('过热', '关注'):
        system_conc = 'WATCHLIST'
        system_reason = f'BC{bc_level}：{bc_reason}'
    else:
        system_conc = 'EXECUTE'
        system_reason = '方向明确·共识支持·价格确认'

    # 第三层：交易员决策（严格按 TRADING_FLOW.md 框架，无框架不输出）
    trading = trader_analysis(mkt, dir_ah, system_conc, price_v, name)
    trader_tag = '同向' if trading.get('product', '-') != '-' else '独立'
    info.append(f'交易员独立判断：{trading["reason"]}')

    return _fin(result, system_conc, system_reason, info, flipped, trading)


def _fin(result, conclusion, reason, info, flipped=False, trading=None):
    result['结论'] = conclusion
    result['理由'] = reason
    result['说明'] = '；'.join(info) if info else reason
    result['步骤'] = info  # 保留原始步骤列表，用于逐行展示
    result['翻转'] = flipped
    if trading is not None:
        result['交易决策'] = trading
    return result


# ── Markdown 解析 ──

_HANDICAP = {
    '受三球半/四球': 3.75, '受三球半': 3.5, '受三球/三球半': 3.25, '受三球': 3.0,
    '受两球半/三球': 2.75, '受两半/三': 2.75, '受两半': 2.5,
    '受两球半': 2.5, '受两球/两球半': 2.25, '受两球/半': 2.25, '受两球': 2.0,
    '受球半/两球': 1.75, '受球半/两': 1.75, '受球半': 1.5,
    '受一/球半': 1.25, '受一球/球半': 1.25, '受一球/半': 1.25, '受一球': 1.0,
    '受半/一': 0.75, '受半球': 0.5, '受平/半': 0.25,
    '平手': 0.0,
    '平/半': -0.25, '半球': -0.5, '半/一': -0.75, '一球': -1.0,
    '一/球半': -1.25, '一球/球半': -1.25, '一球/半': -1.25, '球半': -1.5, '球半/两球': -1.75, '球半/两': -1.75,
    '两球': -2.0, '两球/两球半': -2.25, '两球/半': -2.25,
    '两球半': -2.5, '两球半/三球': -2.75, '两半/三': -2.75,
    '三球': -3.0, '三球/三球半': -3.25, '三球半': -3.5, '三球半/四球': -3.75, '四球': -4.0,
}


def _parse_hcp(s):
    """中文盘口转数值，支持多种格式"""
    s = s.strip().replace(' ', '')
    # 如果是数字（含符号），直接转
    try:
        return float(s)
    except ValueError:
        pass
    # 中文归一化
    s = s.replace('受让', '受')
    return _HANDICAP.get(s)


def _parse_md(text):
    """解析 Markdown 赔率表格为 JSON 列表"""
    import re
    lines = text.strip().split('\n')
    matches = []
    i = 0
    while i < len(lines):
        l = lines[i]
        name = None
        curr = snap = None

        # 检测对阵双方：| 对阵双方 | X vs Y |
        m_vs = re.search(r'\|\s*对阵双方\s*\|\s*(.+?)\s+vs\s+(.+?)\s*\|', l, re.IGNORECASE)
        if m_vs:
            name = f'{m_vs.group(1).strip()} vs {m_vs.group(2).strip()}'

        # 比赛标题：#/## 名字 vs 名字
        m_match = re.match(r'#+\s*(?:比赛\s*\d*[：:]\s*)?(.+?)\s*$', l)
        if m_match and 'vs' in m_match.group(1).lower():
            name = m_match.group(1).strip()
            name = re.sub(r'[（(].*?[）)]', '', name).strip()
            if ' vs ' not in name and ' VS ' in name:
                name = name.replace(' VS ', ' vs ')

        # 对阵信息行：- **对阵**: 哥伦比亚 vs 葡萄牙
        m_vs_info = re.search(r'[-\*]*\s*\*?\*?\s*对阵\s*\*?\*?\s*[：:]\s*(.+?)\s*$', l)
        if m_vs_info and 'vs' in m_vs_info.group(1).lower():
            name = m_vs_info.group(1).strip()
            name = re.sub(r'\s*\[\d+\]', '', name)
            name = re.sub(r'[（(].*?[）)]', '', name).strip()
            if ' vs ' not in name and ' VS ' in name:
                name = name.replace(' VS ', ' vs ')

        # 有比赛名就进入数据采集
        if name:
            curr = {}
            snap = {}
            section = None
            header_cols = []
            j = i + 1

            while j < len(lines):
                l2 = lines[j].strip()

                # 检测章节
                if '欧指' in l2 or '欧赔' in l2:
                    section = 'ml'
                elif '亚指' in l2 or '亚盘' in l2 or '让球' in l2:
                    section = 'ah'
                elif '大小球' in l2 or '大小' in l2:
                    section = 'ou'
                elif l2.startswith('## '):
                    break
                elif re.match(r'^#+\s+\S+.*vs\.?\s+\S+', l2, re.IGNORECASE):
                    break
                elif l2.startswith('|') and '---' not in l2 and section:
                    cols = [c.strip() for c in l2.split('|')[1:-1]]
                    if len(cols) < 5:
                        j += 1
                        continue
                    # 跳过表头行
                    if cols[0] in ('机构', ':---', '---', '机构(初盘)', '公司') or '机构' in cols[0] or '公司' in cols[0]:
                        # 保存表头用于格式检测
                        header_cols = cols
                        j += 1
                        continue
                    if cols[0].startswith(':'):
                        j += 1
                        continue

                    bk = cols[0]
                    # 标准化庄家名
                    if '威廉' in bk:
                        bk = 'William Hill'
                    elif 'Bet365' in bk or 'bet365' in bk:
                        bk = 'Bet365'
                    elif 'singbet' in bk or '皇冠' in bk or 'Crown' in bk or 'sbobet' in bk.lower():
                        bk = 'singbet'
                    elif '澳门' in bk:
                        bk = '澳门彩票'
                    elif 'Pinnacle' in bk or '平博' in bk or 'Pin' in bk:
                        bk = 'Pinnacle'

                    if section == 'ml':
                        # 欧指: 机构 | 初盘_胜 | 初盘_平 | 初盘_负 | 最新_胜 | 最新_平 | 最新_负
                        if len(cols) >= 7:
                            snap.setdefault(bk, {})
                            curr.setdefault(bk, {})
                            try:
                                snap_ml = {'home': _fl(cols[1]), 'draw': _fl(cols[2]), 'away': _fl(cols[3])}
                                curr_ml = {'home': _fl(cols[4]), 'draw': _fl(cols[5]), 'away': _fl(cols[6])}
                                snap[bk]['ML'] = {'1': snap_ml}
                                curr[bk]['ML'] = {'1': curr_ml}
                            except:
                                pass

                    elif section == 'ah':
                        # 亚指：支持两种列顺序
                        # 格式A: 机构 | 盘口 | 主水 | 客水 | 盘口 | 主水 | 客水
                        # 格式B: 机构 | 主水 | 盘口 | 客水 | 主水 | 盘口 | 客水
                        if len(cols) >= 7:
                            snap.setdefault(bk, {})
                            curr.setdefault(bk, {})
                            # 用表头检测：若 header_cols[1] 含"主水"或"客水"→格式B
                            is_fmt_b = False
                            try:
                                h1 = header_cols[1] if header_cols else ''
                                is_fmt_b = '主水' in h1 or '客水' in h1
                            except:
                                pass
                            if is_fmt_b:
                                # 格式B: 水 盘 水 | 水 盘 水
                                snap_hw = _fl(cols[1])
                                snap_hcp = _parse_hcp(cols[2])
                                snap_aw = _fl(cols[3])
                                curr_hw = _fl(cols[4])
                                curr_hcp = _parse_hcp(cols[5])
                                curr_aw = _fl(cols[6])
                            else:
                                # 格式A: 盘 水 水 | 盘 水 水
                                snap_hcp = _parse_hcp(cols[1])
                                snap_hw = _fl(cols[2])
                                snap_aw = _fl(cols[3])
                                curr_hcp = _parse_hcp(cols[4])
                                curr_hw = _fl(cols[5])
                                curr_aw = _fl(cols[6])
                            if snap_hcp is not None:
                                snap[bk]['Spread'] = {'1': {'line': snap_hcp, 'home': snap_hw, 'away': snap_aw}}
                            if curr_hcp is not None:
                                curr[bk]['Spread'] = {'1': {'line': curr_hcp, 'home': curr_hw, 'away': curr_aw}}

                    elif section == 'ou':
                        # 大小球：同样支持两种列顺序
                        # 格式A: 机构 | 界线 | 大球 | 小球 | 界线 | 大球 | 小球
                        # 格式B: 机构 | 大球 | 界线 | 小球 | 大球 | 界线 | 小球
                        if len(cols) >= 7:
                            snap.setdefault(bk, {})
                            curr.setdefault(bk, {})
                            # 用表头检测：若cols[1]含"大球"/"小球"→格式B
                            is_fmt_b = False
                            try:
                                h1 = header_cols[1] if header_cols else ''
                                is_fmt_b = '大球' in h1 or '小球' in h1
                            except:
                                pass
                            if is_fmt_b:
                                # 格式B: 大 界 小 | 大 界 小
                                snap_ov = _fl(cols[1])
                                snap_ln = _fl(cols[2])
                                snap_ud = _fl(cols[3])
                                curr_ov = _fl(cols[4])
                                curr_ln = _fl(cols[5])
                                curr_ud = _fl(cols[6])
                            else:
                                # 格式A: 界 大 小 | 界 大 小
                                snap_ln = _fl(cols[1])
                                snap_ov = _fl(cols[2])
                                snap_ud = _fl(cols[3])
                                curr_ln = _fl(cols[4])
                                curr_ov = _fl(cols[5])
                                curr_ud = _fl(cols[6])
                            if snap_ln is not None:
                                snap[bk]['Totals'] = {'1': {'line': snap_ln, 'home': snap_ov, 'under': snap_ud}}
                            if curr_ln is not None:
                                curr[bk]['Totals'] = {'1': {'line': curr_ln, 'home': curr_ov, 'under': curr_ud}}

                j += 1

            if curr:
                matches.append({'name': name, 'curr': curr, 'snap': snap})
            i = j
            continue
        i += 1

    return matches


def _log_match(r, name):
    """存档比赛分析结果到 results_log.jsonl"""
    import os, datetime
    log_path = '/var/minis/skills/六扇门/results_log.jsonl'
    entry = {
        'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'name': name,
        'conclusion': r.get('结论'),
        'direction': r.get('方向'),
        'flipped': r.get('翻转', False),
        'bl': r.get('_bl'),
        'price': r.get('价格'),
        'euro': r.get('欧赔'),
        'BC': r.get('BC判决'),
        'reason': r.get('理由'),
    }
    with open(log_path, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


# ── CLI ──

def main():
    if len(sys.argv) > 1:
        # 尝试 JSON，失败则尝试 Markdown
        with open(sys.argv[1]) as f:
            raw = f.read()
        try:
            data = json.loads(raw)
        except:
            data = _parse_md(raw)
        for item in (data if isinstance(data, list) else [data]):
            r = analyze(item['name'], item)
            _print(r, item['name'])
            _log_match(r, item['name'])
    else:
        import shutil
        w = shutil.get_terminal_size().columns
        print("V7 Final · 五步流程".center(w))
        print("=" * w)
        raw = ''
        for l in sys.stdin:
            raw += l
        raw = raw.strip()
        if not raw:
            return
        try:
            data = json.loads(raw)
        except:
            data = _parse_md(raw)
        for item in (data if isinstance(data, list) else [data]):
            r = analyze(item['name'], item)
            _print(r, item['name'])
            _log_match(r, item['name'])


def _print(r, name):
    vi = {'EXECUTE': '✅ EXECUTE', 'PASS': '🚫 PASS', 'WATCHLIST': '🔶 WATCHLIST'}
    conc = r.get('结论', '')
    sig = r.get('信号', '') or ''
    euro_s = r.get('欧赔明细', '') or ''
    note = r.get('说明', '') or ''
    reason = r.get('理由', '')
    price_l = r.get('价格结论', '')
    euro_v = r.get('欧赔', '')

    dir_label = r.get('方向', '')
    flipped = r.get('翻转', False)
    bl_r = r.get('_bl', None)
    if dir_label in ('+', '-'):
        team_r = name.split(' vs ')[0] if dir_label == '+' else name.split(' vs ')[1]
        team_r = team_r.split('（')[0].split('(')[0].strip()
        if bl_r is not None:
            abs_line = f'{abs(bl_r):.2f}'.rstrip('0').rstrip('.')
            if flipped:
                d = f'{team_r}+{abs_line} ⚡'
            else:
                # 非翻转：方向方让球/受让取决于线位符号
                if dir_label == '+':
                    line_sign = '-' if bl_r < 0 else '+'
                else:
                    line_sign = '-' if bl_r > 0 else '+'
                d = f'{team_r}{line_sign}{abs_line}'
        else:
            d = '—'
    else:
        d = '—'

    # Consensus 映射
    con_map = {'支持': '强', '中性': '中性', '反对': '否决'}
    con = con_map.get(euro_v, '—')

    # Price 映射
    prc_map = {'偏便宜': '便宜', '合理': '合理', '偏贵': '存疑'}
    prc = prc_map.get(r.get('价格', ''), '—')

    # Risk 级别
    risk_map = {'PASS': 'PASS', 'WATCHLIST': '中', 'EXECUTE': '低'}
    rsk = risk_map.get(conc, '—')

    # Bookmaker 方向温度
    bc_v = r.get('BC判决', '')
    bm = bc_v if bc_v else '—'
    bc_d = r.get('BC理由', '')
    bc_short = ''
    if bc_d:
        # 提取 BC1-BC4 摘要
        parts = [p.strip() for p in bc_d.split('|')]
        bc_short = ' | '.join(p.split('（')[0] if '（' in p else p for p in parts)

    lines = [
        '',
        '  ' + '━' * 50,
        f'  {name}',
        f'  {vi.get(conc, conc)}',
        '  ' + '━' * 50,
    ]
    # 第一层：🧮 庄家平衡（纯数据）
    bal = r.get('盘口管理', [])
    if bal:
        for item in bal:
            lines.append(f'    {item}')
        lines.append('  ' + chr(0x2500) * 50)
    # 第二层：系统结论（分析）
    best_line = r.get('_best_line', '')
    lines += [
        f'  方向 Direction:       {d}',
        f'  盘口 Price:           {best_line}' if best_line else '',
        f'  共识 Consensus:       {con}',
        f'  价格 Price:           {prc}',
        f'  风险 Risk:            {rsk}',
        f'  大小球 OU:            {r.get("大小球","") or "-"}',
        f'  BC 方向温度:          {bm}' + (f'  [{bc_short}]' if bc_short else ''),
        f'  合拍度:               {r.get("合拍度","—")}' + (f'  ({", ".join(r.get("合拍度理由",[]))})' if r.get('合拍度理由') else ''),
        f'  破绽:                 {"、".join(r.get("破绽",[])) if r.get("破绽") else "无"}',
        '  ' + chr(0x2500) * 50,
        f'  结论：{reason}',
    ]
    # 展开详细步骤
    steps = r.get('步骤', [])
    if steps:
        lines.append('  ---')
        for i, s in enumerate(steps):
            s = s.strip()
            if not s:
                continue
            lines.append(f'  第{i+1}步 | {s}')
    # 第三层：🎯 盘口交易员视角（判断）
    ta = r.get('交易决策', {})
    if ta:
        lines.append('  ' + chr(0x2500) * 50)
        lines.append('  🎯 盘口交易员视角（强制框架）')
        lines.append(f'  Q1 庄家现在在干什么？   {ta.get("q1", "")}')
        lines.append(f'  Q2 庄家为什么这样定价？ {ta.get("q2", "")}')
        lines.append(f'  Q3 庄家承担哪边风险？   {ta.get("q3", "")}')
        lines.append(f'  Q4 哪个结果最符合庄家赔付利益？ {ta.get("q4", "")}')
        lines.append('')
        trade_side = ta.get('trade_side', 'PASS')
        product = ta.get('product', '-')
        if product and product != '-':
            lines.append(f'  站在庄家视角：{trade_side} 更值得交易')
            lines.append(f'  推荐标的：{product}')
            lines.append(f'  建议仓位：{ta["size"]}')
        else:
            lines.append(f'  站在庄家视角：无明确交易方向（PASS）')
        lines.append(f'  决策依据：{ta.get("reason", "")}')
    for l in lines:
        print(l)


if __name__ == '__main__':
    main()
