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

import json, sys

BK_CORE = ['Pinnacle', 'Bet365', 'singbet']
BK_EURO = ['Pinnacle', 'Bet365', 'singbet']
BK_LABEL = {'Pinnacle': 'Pin', 'Bet365': '365', 'singbet': '皇冠',
            '澳门彩票': '澳门'}
BK_FULL = {'Pinnacle': 'Pinnacle', 'Bet365': 'Bet365', 'singbet': '皇冠',
           '澳门彩票': '澳门彩票'}

def _fl(v):
    if v is None:
        return None
    v = str(v).strip()
    # 去掉 (↑) (↓) 等升降标记
    if '(' in v:
        v = v[:v.index('(')].strip()
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
        else:
            dir_ah = '-'; maj = votes_agst; min_ = votes_for

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


def check_ou(mkt):
    """大小球：返回 (方向, 说明)
    交叉确认：线位方向 + 水位方向配合
    """
    cv = ov = None
    for bk in ['Pinnacle', 'Bet365', 'singbet']:
        cv = next(iter(mkt.get('curr', {}).get(bk, {}).get('Totals', {}).values()), None)
        ov = next(iter(mkt.get('snap', {}).get(bk, {}).get('Totals', {}).values()), None)
        if cv is not None and ov is not None:
            break
    if cv is None or ov is None:
        return None, None

    cl = _fl(cv.get('line'))
    ol = _fl(ov.get('line'))
    ch = _fl(cv.get('home'))    # 大球（over）水
    oh = _fl(ov.get('home'))
    cu = _fl(cv.get('under'))   # 小球（under）水
    ou_ = _fl(ov.get('under'))

    if cl is None or ol is None:
        return None, None

    # 线位升
    if cl > ol:
        if ch is not None and oh is not None and ch < oh:
            return '大球', f'升盘+大球水降（{ol:.2f}→{cl:.2f}，{oh:.2f}→{ch:.2f}）'
        elif ch is not None and oh is not None and ch > oh:
            return '小球', f'升盘+大球水升诱盘（{ol:.2f}→{cl:.2f}，{oh:.2f}→{ch:.2f}）'
        return '大球', f'升盘（{ol:.2f}→{cl:.2f}）'
    
    # 线位退
    if cl < ol:
        if cu is not None and ou_ is not None and cu < ou_:
            return '小球', f'退盘+小球水降（{ol:.2f}→{cl:.2f}，{ou_:.2f}→{cu:.2f}）'
        elif cu is not None and ou_ is not None and cu > ou_:
            return '大球', f'退盘+小球水升诱盘（{ol:.2f}→{cl:.2f}，{ou_:.2f}→{cu:.2f}）'
        return '小球', f'退盘（{ol:.2f}→{cl:.2f}）'
    
    # 线位不动，看水位
    if ch and oh:
        if ch > oh:
            return '小球', f'线位不动+大球水升（{oh:.2f}→{ch:.2f}）'
        if ch < oh:
            return '大球', f'线位不动+大球水降（{oh:.2f}→{ch:.2f}）'
    if cu and ou_:
        if cu < ou_:
            return '小球', f'线位不动+小球水降（{ou_:.2f}→{cu:.2f}）'
        if cu > ou_:
            return '大球', f'线位不动+小球水升（{ou_:.2f}→{cu:.2f}）'
    
    return '中性', '线位水位未动'


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
    #  R5 亚洲盘口   — Pin=365 但皇冠反向 → 亚洲不给确认 → PASS
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

    # R5: 亚洲盘口否决 — 专业+大众一致，但皇冠反向
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
    """盘口管理分析：拆线位结构、庄家工具、结算方式
    不猜庄家意图，只描述盘口设置和赔付结构
    返回 (push_line, items: list[str])
    """
    def _line(bk, src):
        sp = src.get(bk, {}).get('Spread', {})
        v = next(iter(sp.values()), {}) if len(sp) == 1 else {}
        return _fl(v.get('line'))

    def _fmt(v):
        s = f'{abs(v):.2f}'.rstrip('0')
        return s[:-1] if s.endswith('.') else s

    curr_pin = _line('Pinnacle', mkt.get('curr', {}))
    snap_pin = _line('Pinnacle', mkt.get('snap', {}))
    home_name = name.split(' vs ')[0].strip()
    away_name = name.split(' vs ')[1].strip()

    ou_val = None
    for bk in ['Pinnacle', 'Bet365']:
        cv = next(iter(mkt.get('curr', {}).get(bk, {}).get('Totals', {}).values()), None)
        if cv is not None:
            ou_val = _fl(cv.get('line'))
            break

    items = []

    if curr_pin is None:
        return '', items

    # ── 线位拆解 ──
    ab = abs(curr_pin)
    # 判断方向方是让球方还是受让方（基于线位符号）
    dir_on_fav = False
    if curr_pin != 0:
        if dir_ah == '+' and curr_pin < 0:
            dir_on_fav = True
        elif dir_ah == '-' and curr_pin > 0:
            dir_on_fav = True
    team = home_name if dir_ah == '+' else away_name
    sign = '-' if dir_on_fav else '+'

    # 拆解盘口结构
    quarter = (ab * 4) % 4  # 0=整数盘, 1=0.25, 2=0.5, 3=0.75
    if quarter == 0:
        split = f'{team}{sign}{_fmt(curr_pin)} = 整数盘，无拆分'
    elif quarter == 2:
        split = f'{team}{sign}{_fmt(curr_pin)} = 半注{team}{sign}{_fmt(ab-0.25)} + 半注{team}{sign}{_fmt(ab+0.25)}'
    elif quarter == 1:
        if abs(curr_pin) == 0.25:
            split = f'{team}{sign}{_fmt(curr_pin)} = 半注{team}{sign}{_fmt(ab-0.25)} + 半注{team}0'
        else:
            split = f'{team}{sign}{_fmt(curr_pin)} = 半注{team}{sign}{_fmt(ab-0.25)} + 半注{team}{sign}{_fmt(ab+0.25)}'
    else:  # quarter == 3 (0.75)
        split = f'{team}{sign}{_fmt(curr_pin)} = 半注{team}{sign}{_fmt(ab-0.25)} + 半注{team}{sign}{_fmt(ab+0.25)}'
    items.append(split)

    # ── 走水点/输半分析 ──
    if quarter == 0:
        items.append(f'走水点：{team} 赢 {_fmt(ab)} 球')
    elif not dir_on_fav:
        items.append(f'拆分：{team}{sign}{_fmt(curr_pin)} 受让方，1球差输半')
    else:
        items.append(f'输半线：{team} 赢 {_fmt(ab-0.25)} 球输一半，{team} 赢 {_fmt(ab+0.25)} 球全收')

    # ── 庄家工具分析（线位/水位/欧赔变动） ──
    tools = []

    # 线位变动
    if snap_pin is not None:
        move = abs(curr_pin - snap_pin)
        if move >= 0.25:
            if curr_pin < snap_pin:
                tools.append(f'推盘{_fmt(move)}格（{_fmt(snap_pin)} → {_fmt(curr_pin)}）')
            else:
                tools.append(f'退盘{_fmt(move)}格（{_fmt(snap_pin)} → {_fmt(curr_pin)}）')
        else:
            tools.append('线位不动')

    # 水位变动
    snap_sp = next(iter(mkt.get('snap', {}).get('Pinnacle', {}).get('Spread', {}).values()), {})
    curr_sp = next(iter(mkt.get('curr', {}).get('Pinnacle', {}).get('Spread', {}).values()), {})
    # 翻转后 dir_ah 已指向受让方，直接取方向方水位
    wkey = 'home' if dir_ah == '+' else 'away'
    snap_w = _fl(snap_sp.get(wkey))
    curr_w = _fl(curr_sp.get(wkey))
    if snap_w and curr_w and abs(curr_w - snap_w) >= 0.02:
        diff = curr_w - snap_w
        direction = '升' if diff > 0 else '降'
        tools.append(f'水位{direction}{abs(diff):.2f}（{snap_w} → {curr_w}）')

    # 欧赔变动
    s_ml = _gm('Pinnacle', mkt.get('snap', {}))
    c_ml = _gm('Pinnacle', mkt.get('curr', {}))
    if s_ml and c_ml:
        fav = 'home' if dir_ah == '+' else 'away'
        snap_o = _fl(s_ml.get(fav))
        curr_o = _fl(c_ml.get(fav))
        if snap_o and curr_o:
            chg = (snap_o - curr_o) / snap_o
            if abs(chg) >= 0.02:
                direction = '降' if chg > 0 else '涨'
                tools.append(f'欧赔{direction}{abs(chg)*100:.0f}%（{snap_o} → {curr_o}）')
            else:
                tools.append('欧赔不动')

        # 平赔
        draw_s = _fl(s_ml.get('draw'))
        draw_c = _fl(c_ml.get('draw'))
        if draw_s and draw_c:
            dchg = (draw_s - draw_c) / draw_s
            if abs(dchg) >= 0.03:
                dd = '降' if dchg > 0 else '涨'
                tools.append(f'平赔{dd}{abs(dchg)*100:.0f}%')

    if tools:
        items.append('庄家工具：' + ' | '.join(tools))

    # ── 大小球配合（始终输出） ──
    ou_summary = []
    for bk, label in [('Pinnacle', 'Pin'), ('Bet365', '365'), ('singbet', '皇冠')]:
        sc = next(iter(mkt.get('snap', {}).get(bk, {}).get('Totals', {}).values()), None)
        cc = next(iter(mkt.get('curr', {}).get(bk, {}).get('Totals', {}).values()), None)
        if sc and cc:
            sl = _fl(sc.get('line'))
            cl = _fl(cc.get('line'))
            so = _fl(sc.get('home'))  # 大球水
            co = _fl(cc.get('home'))
            if sl is not None and cl is not None and so is not None and co is not None:
                line_chg = '升盘' if cl > sl else '退盘' if cl < sl else '线位不动'
                water_chg = f"{'涨' if co > so else '降'}{abs(co-so):.2f}" if abs(co-so)>=0.02 else '水不动'
                ou_summary.append(f'{label}{_fmt(sl)}→{_fmt(cl)}({line_chg},{water_chg})')
    if ou_summary:
        items.append('大小球：' + ' | '.join(ou_summary))

    # ── 结算简表（4种典型赛果） ──
    settlement = []
    win_margin = ab  # 赢球数 = 盘口深度
    if dir_on_fav:
        # 让球方
        if quarter == 0:
            settlement.append(f'{team} 赢 {_fmt(ab)} 球 → 走水')
            settlement.append(f'{team} 赢 {_fmt(ab+1)} 球 → 全收')
        elif quarter == 2:
            settlement.append(f'{team} 赢 {_fmt(ab+0.25)} 球 → 全收')
            settlement.append(f'{team} 赢 {_fmt(ab-0.25)} 球 → 输半')
        else:
            settlement.append(f'{team} 赢 {_fmt(ab+0.25)} 球 → 全收')
            settlement.append(f'{team} 赢 {_fmt(ab-0.25)} 球 → 输半')
        settlement.append(f'{team} 输或平 → 全输')
    else:
        # 受让方
        loss_margin = ab
        settlement.append(f'{team} 赢或平 → 全收')
        settlement.append(f'{team} 输 1 球 → 输半')
        settlement.append(f'{team} 输 2 球 → 全输')

    if settlement:
        for s in settlement[:3]:
            items.append(f'结算：{s}')

    return '', items


# ═══════════════════════════════════════════
#  主分析
# ═══════════════════════════════════════════

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
            info.append(f'平局信号（{src_name}），方向方已是受让方，无需翻转')
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
        _, bal_items = bookmaker_balance(mkt, dir_ah, flipped, name)
        result['盘口管理'] = bal_items

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

    # ── ⑥ 最终决策（Final Decision）──
    if flipped:
        return _fin(result, 'WATCHLIST', '翻转·价格确认', info, flipped)
    if euro_v == '反对':
        return _fin(result, 'PASS', '共识否决：欧赔反对', info)
    if euro_v == '中性':
        return _fin(result, 'WATCHLIST', '共识中立', info)
    if bc_level in ('过热', '关注'):
        return _fin(result, 'WATCHLIST', f'BC{bc_level}：{bc_reason}', info, flipped)
    return _fin(result, 'EXECUTE', '方向明确·共识支持·价格确认', info)


def _fin(result, conclusion, reason, info, flipped=False):
    result['结论'] = conclusion
    result['理由'] = reason
    result['说明'] = '；'.join(info) if info else reason
    result['翻转'] = flipped
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
                    elif 'singbet' in bk:
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
                                snap_ml = {'home': float(cols[1]), 'draw': float(cols[2]), 'away': float(cols[3])}
                                curr_ml = {'home': float(cols[4]), 'draw': float(cols[5]), 'away': float(cols[6])}
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
                            # 自动检测：如果cols[1]是数字→格式B(水在前)，否则格式A(盘在前)
                            try:
                                float(cols[1])
                                # 格式B: 水 盘 水 | 水 盘 水
                                snap_hw = _fl(cols[1])
                                snap_hcp = _parse_hcp(cols[2])
                                snap_aw = _fl(cols[3])
                                curr_hw = _fl(cols[4])
                                curr_hcp = _parse_hcp(cols[5])
                                curr_aw = _fl(cols[6])
                            except (ValueError, TypeError):
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
        f'  方向 Direction:       {d}',
        f'  共识 Consensus:       {con}',
        f'  价格 Price:           {prc}',
        f'  风险 Risk:            {rsk}',
        f'  大小球 OU:            {r.get("大小球","") or "-"}',
        f'  BC 方向温度:          {bm}' + (f'  [{bc_short}]' if bc_short else ''),
        '  ' + chr(0x2500) * 50,
        f'  {reason}',
    ]
    if sig:
        lines.append(f'  亚盘投票 => {sig}')
    if note and note != reason:
        lines.append(f'  → {note}')
    # 盘口管理
    bal = r.get('盘口管理', [])
    if bal:
        lines.append('  ' + chr(0x2500) * 50)
        lines.append('  📐 盘口管理（Line Management）')
        for item in bal:
            lines.append(f'    {item}')
    for l in lines:
        print(l)


if __name__ == '__main__':
    main()
