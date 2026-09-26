#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_docs.py — ST Agent 文档一致性一键自检（stdlib only）

复用 render_ledger 的解析逻辑，跑五项检查并给出 PASS/FAIL：
  1. 断链      全项目 .md 相对链接（先剔除围栏代码块与行内代码，避免示例路径误报）
  2. 依赖图    从 tasks/*.md 反构：悬空依赖 / 环 / 就绪集（队首）
  3. 账本同步  当前 任务账本.md 与 build_ledger() 生成结果比对（忽略时间戳行）
  4. 过期措辞  运营文档里是否残留重构前的旧关键词（警告，不判失败）
  5. 假设完整  doing/blocked/done 叶子任务是否有 `## 假设与前提` 实填节（警告；--strict 下判失败）
  6. 接口面    doing/blocked/done 叶子任务是否有 `## 接口面` 实填节（警告；--strict 下判失败）
  7. 集成关卡  每个里程碑是否有 T-INT-* 关卡任务、且其 depends_on 覆盖本里程碑全部叶子任务
               （警告；--strict 下判失败）

用法：
  python tools/verify_docs.py            # 只读校验
  python tools/verify_docs.py --fix      # 若账本落后，顺手 render 刷新
  python tools/verify_docs.py --strict   # 把「过期措辞/账本落后」也计入失败

退出码：0=通过（无硬失败），1=有硬失败。
"""
import os, re, sys, argparse
sys.dont_write_bytecode = True   # 别在 tools/ 生成 __pycache__，保持目录干净

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import render_ledger as rl          # 复用：PM / TASKS / DONE / LEDGER / scan / build_ledger / ID_RE

ROOT = os.path.dirname(rl.PM)
MD_LINK = re.compile(r'\[[^\]]*\]\(([^)]+)\)')
TS = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

# 检查 1：断链（代码感知）
def strip_code(txt):
    txt = re.sub(r'```.*?```', '', txt, flags=re.S)      # 围栏
    txt = re.sub(r'`[^`\n]*`', '', txt)                   # 行内
    return txt

def link_target(t):
    t = t.strip().strip('"')
    if not t or t.startswith(('#', 'http', 'mailto:', 'data:', 'tel:')): return None
    t = t.split()[0].strip('<>').strip('"\'').split('#', 1)[0].split('?', 1)[0].strip()
    return t or None

def check_links():
    broken = []; cnt = 0; md = 0
    for dp, ds, fs in os.walk(ROOT):
        ds[:] = [d for d in ds if d != '.git']
        for fn in fs:
            if not fn.endswith('.md'): continue
            md += 1; full = os.path.join(dp, fn)
            for m in MD_LINK.finditer(strip_code(open(full, encoding='utf-8').read())):
                p = link_target(m.group(1))
                if not p or p.startswith(('/', '\\\\')): continue
                cnt += 1
                if not os.path.exists(os.path.normpath(os.path.join(dp, p))):
                    broken.append((os.path.relpath(full, ROOT), m.group(1)))
    return md, cnt, broken

# 检查 2：依赖图
def check_graph():
    tasks = rl.scan(rl.TASKS) + rl.scan(rl.DONE)
    deps = {t['id']: t['depends_on'] for t in tasks}
    dangling = [(a, b) for a, ds in deps.items() for b in ds if b not in deps]
    color = {t: 0 for t in deps}; cycles = []
    sys.setrecursionlimit(10000)
    def dfs(u, st):
        color[u] = 1; st.append(u)
        for v in deps.get(u, []):
            if v not in color: continue
            if color[v] == 1: cycles.append(st[st.index(v):] + [v])
            elif color[v] == 0: dfs(v, st)
        st.pop(); color[u] = 2
    for t in list(deps):
        if color[t] == 0: dfs(t, [])
    ready = [t['id'] for t in rl.compute_status(tasks)]   # compute_status 返回就绪集
    return len(tasks), dangling, cycles, sorted(ready)

# 检查 3：账本同步
def check_ledger(fix):
    if not os.path.exists(rl.LEDGER):
        return "MISSING", None
    cur = TS.sub('TS', open(rl.LEDGER, encoding='utf-8').read())
    new, na, nr, nready = rl.build_ledger()
    newn = TS.sub('TS', new)
    if cur.strip() == newn.strip():
        return "SYNC", f"活跃{na}/归档{nr}/就绪{nready}"
    if fix:
        open(rl.LEDGER, 'w', encoding='utf-8').write(new)
        return "FIXED", "账本已刷新"
    return "STALE", "账本落后于 tasks/ 真相源，请跑 render"

# 检查 4：过期措辞（仅运营文档，警告级）
STALE_PATTERNS = [
    (re.compile(r'五步|六步'), '工作流应为「七步」'),
    (re.compile(r'十条铁律|补\s*[34]\s*条'), '宪法应为「11 条 / 补 5 条」'),
    (re.compile(r'唯一调度入口'), '账本已改为「派生视图」'),
    (re.compile(r'M0/M1/M2(?!/M3)'), '里程碑应为 M0–M4'),
    (re.compile(r'登记账本'), '措辞应为「改 tasks 文件后跑 render」'),
]
OPS_DOCS = ['README.md', '工程宪法.md', '工作流.md', 'AI-Coding项目管理方案.md',
            '../CLAUDE.md']

def check_stale():
    hits = []
    for rel in OPS_DOCS:
        p = os.path.normpath(os.path.join(rl.PM, rel))
        if not os.path.exists(p): continue
        txt = strip_code(open(p, encoding='utf-8').read())
        for rx, why in STALE_PATTERNS:
            if rx.search(txt):
                hits.append((os.path.relpath(p, ROOT), rx.pattern, why))
    return hits

# 检查 5：假设完整（doing/blocked/done 叶子任务须有实填的 `## 假设与前提` 节）
# 2026-09-25 机制落地前已 done 的历史任务免检（不追补），之后新增的 done 一律必检。
GRANDFATHERED_NO_ASSUME = {
    "T-SC-001", "T-SC-001.1", "T-SC-001.2", "T-SC-001.3", "T-SC-001.4", "T-SC-001.5",
    "T-L0-001", "T-L0-002", "T-L0-003", "T-L0-004", "T-L0-005", "T-L0-005.1",
}
ASSUME_HEAD = re.compile(r"^##\s*假设与前提", re.M)
ASSUME_PLACEHOLDER = re.compile(r"暂无|待.{0,6}对齐|待补|（④ 对齐时补")

def _check_task_section(head_rx, placeholder_rx, label, exempt):
    """doing/blocked/done 叶子任务的 `## <label>` 节是否实填（父任务与豁免清单跳过）。"""
    tasks = rl.scan(rl.TASKS)   # 只查活跃区；已归档 tasks/done/ 不查
    parents = {t["parent"] for t in tasks if t["parent"]}
    missing = []
    for t in tasks:
        if t["status"] not in ("doing", "blocked", "done"): continue
        if t["id"] in parents or t["id"] in exempt: continue
        _, body = rl.read_fm(t["path"])
        m = head_rx.search(body or "")
        if not m:
            missing.append((t["id"], f"缺 `## {label}` 节")); continue
        sect = (body or "")[m.end():]
        nxt = re.search(r"^##\s+", sect, re.M)
        sect = sect[:nxt.start()] if nxt else sect
        text = re.sub(r"[`\s]", "", strip_code(sect))
        if not text or placeholder_rx.search(sect):
            missing.append((t["id"], f"{label}节仍为占位，未实填"))
    return missing

def check_assumptions():
    return _check_task_section(ASSUME_HEAD, ASSUME_PLACEHOLDER, "假设与前提",
                               GRANDFATHERED_NO_ASSUME)

# 检查 6：接口面完整（2026-09-26 与检查 5 同批落地）
# 豁免：机制落地（2026-09-26）前已 done 的历史任务不追补——即检查 5 的 12 个，外加
# 09-25/09-26 之间完成、早于本机制的 T-L0-006 与 T-L1-001.1/.2/.3。
IFACE_HEAD = re.compile(r"^##\s*接口面", re.M)
IFACE_PLACEHOLDER = re.compile(r"暂无|待.{0,6}对齐|待补|（④ 对齐时补")
GRANDFATHERED_NO_IFACE = GRANDFATHERED_NO_ASSUME | {
    "T-L0-006", "T-L1-001.1", "T-L1-001.2", "T-L1-001.3",
}

def check_interfaces():
    return _check_task_section(IFACE_HEAD, IFACE_PLACEHOLDER, "接口面",
                               GRANDFATHERED_NO_IFACE)

# 检查 7：集成关卡（每里程碑必有 T-INT-* 且依赖覆盖本里程碑全部叶子任务）
INT_RE = re.compile(r"^T-INT-\d{3}")

def _gate_skipped(t):
    """`gate: skip` 的任务不计入里程碑收口的依赖覆盖（长跑/运营类，理由须写进备注）。"""
    fm, _ = rl.read_fm(t["path"])
    return (fm.get("gate", "") or "").strip().lower() == "skip"

def check_integration_gates():
    """里程碑集成关卡完整性（2026-09-26 机制）。

    ① 该里程碑有活跃任务 → 必须有同里程碑的 T-INT-* 关卡任务；
    ② 关卡任务的 depends_on 必须覆盖该里程碑全部非 INT 叶子任务的 id
       （标 `gate: skip` 的长跑/运营任务除外）。
    该里程碑已归档（无活跃任务）时跳过。
    """
    tasks = rl.scan(rl.TASKS)
    parents = {t["parent"] for t in tasks if t["parent"]}
    leaves = [t for t in tasks if t["id"] not in parents]
    issues = []
    for mid, _name, _goal in rl.MILESTONES:
        ms = [t for t in leaves if t["milestone"] == mid and not INT_RE.match(t["id"])
              and not _gate_skipped(t)]
        if not ms: continue
        gates = [t for t in tasks if INT_RE.match(t["id"]) and t["milestone"] == mid]
        if not gates:
            issues.append((mid, f"缺集成关卡任务（应为 T-INT-*，覆盖 {len(ms)} 个任务）"))
            continue
        deps = set(gates[0]["depends_on"])
        uncovered = [t["id"] for t in ms if t["id"] not in deps]
        if uncovered:
            issues.append((gates[0]["id"], "依赖未覆盖本里程碑任务：" + ", ".join(uncovered)))
    return issues

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # 兼容 Windows GBK 控制台，避免 emoji/中文崩溃
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--fix', action='store_true'); ap.add_argument('--strict', action='store_true')
    a = ap.parse_args()

    hard_fail = 0; warn = 0
    md, cnt, broken = check_links()
    print(f"[1] 断链      : {md} 文件 / {cnt} 链接 / {len(broken)} 断链")
    for r, t in broken[:30]:
        print(f"      ✗ {r} -> {t}"); hard_fail += 1

    n, dangling, cycles, ready = check_graph()
    print(f"[2] 依赖图    : {n} 任务 / 悬空{len(dangling)} / 环{len(cycles)} / 就绪集 {ready}")
    for d in dangling: print(f"      ✗ 悬空依赖 {d[0]} -> {d[1]}"); hard_fail += 1
    for c in cycles:   print(f"      ✗ 依赖环 {' -> '.join(c)}"); hard_fail += 1

    state, note = check_ledger(a.fix)
    print(f"[3] 账本同步  : {state}  ({note})")
    if state == 'STALE': warn += 1
    if state == 'MISSING': hard_fail += 1

    hits = check_stale()
    print(f"[4] 过期措辞  : {len(hits)} 处")
    for f, p, why in hits: print(f"      ⚠ {f} 命中「{p}」— {why}"); warn += 1

    missing = check_assumptions()
    print(f"[5] 假设完整  : {len(missing)} 缺失")
    for tid, why in missing: print(f"      ⚠ {tid} {why}"); warn += 1

    iface = check_interfaces()
    print(f"[6] 接口面    : {len(iface)} 缺失")
    for tid, why in iface: print(f"      ⚠ {tid} {why}"); warn += 1

    gates = check_integration_gates()
    print(f"[7] 集成关卡  : {len(gates)} 处")
    for tid, why in gates: print(f"      ⚠ {tid} {why}"); warn += 1

    fail = hard_fail + (warn if a.strict else 0)
    print("\n结果：", "PASS ✅" if fail == 0 else f"FAIL ❌（硬失败 {hard_fail}，警告 {warn}{'' if not a.strict else '·strict'}）")
    sys.exit(0 if fail == 0 else 1)

if __name__ == '__main__':
    main()
