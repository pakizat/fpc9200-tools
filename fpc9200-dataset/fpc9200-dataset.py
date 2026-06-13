#!/usr/bin/env python3
"""
FPC 9200 数据集收集与匹配分析工具
===================================

功能:
  1. 录入模板指纹 (enroll) — 7 张图像存为 F9RM 模板
  2. 录制正确手指样本集 — 每按一次保存一个样本
  3. 录制错误手指样本集 — 每按一次保存一个样本
  4. 匹配计算 — 所有样本 vs 模板，输出分数报告

用法:
  python3 fpc9200-dataset.py --enroll-template     # 录入模板
  python3 fpc9200-dataset.py --capture genuine      # 录制正确手指样本
  python3 fpc9200-dataset.py --capture impostor     # 录制错误手指样本
  python3 fpc9200-dataset.py --match                # 匹配计算并输出报告
  python3 fpc9200-dataset.py --list                 # 查看样本列表
  python3 fpc9200-dataset.py --report               # 查看报告列表
  python3 fpc9200-dataset.py --analyze <file>       # 分析已有报告
"""

import subprocess
import json
import os
import sys
import time
import re
from datetime import datetime

# ============ 配置 ============
DATA_DIR = os.path.expanduser("~/projects/fingerprint-driver/data/fpc9200-dataset")
TEMPLATE_FILE = "/var/lib/fprint/skyworker/fpcmoc/0/7"
FPRINTD_ENROLL = "/home/skyworker/fprintd/build/utils/fprintd-enroll"
FPRINTD_VERIFY = "/home/skyworker/fprintd/build/utils/fprintd-verify"
FPRINTD_DELETE = "/home/skyworker/fprintd/build/utils/fprintd-delete"
USER = "skyworker"
FINGER = "right-index-finger"
IMAGE_SIZE = 112 * 88  # 9856 bytes

# ============ 日志解析 ============
RE_TEMPLATE = re.compile(
    r'10a5:9200 RAW template\[(\d+)/(\d+)\] '
    r'score=(\d+) raw=(\d+) center=(\d+) '
    r'offset=(-?\d+),(-?\d+)'
)
RE_MATCH = re.compile(
    r'10a5:9200 RAW match score: (\d+) raw=(\d+) center=(\d+) '
    r'\(threshold (\d+), center-threshold (\d+), '
    r'template (\d+)/(\d+), offset (-?\d+),(-?\d+)'
)
RE_RESULT = re.compile(r'Verify result: (verify-match|verify-no-match)')


def ensure_fprintd():
    """确保本地 fprintd 在运行"""
    try:
        subprocess.run(
            "busctl --system list 2>/dev/null | grep -q fprint",
            shell=True, check=True, capture_output=True
        )
    except subprocess.CalledProcessError:
        print("Starting fprintd...")
        subprocess.run("sudo -n killall fprintd 2>/dev/null", shell=True)
        time.sleep(2)
        subprocess.Popen(
            "sudo -n env G_MESSAGES_DEBUG=all STATE_DIRECTORY=/var/lib/fprint "
            "/home/skyworker/fprintd/build/src/fprintd -t",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(3)


def run_cmd(cmd, timeout=300):
    """运行命令并返回输出"""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )
    return result.stdout + result.stderr


def parse_verify_output(text):
    """解析 verify 输出"""
    templates = []
    for m in RE_TEMPLATE.finditer(text):
        templates.append({
            "template": int(m.group(1)),
            "total": int(m.group(2)),
            "score": int(m.group(3)),
            "raw_score": int(m.group(4)),
            "center_score": int(m.group(5)),
            "dx": int(m.group(6)),
            "dy": int(m.group(7)),
        })

    best = None
    for m in RE_MATCH.finditer(text):
        best = {
            "score": int(m.group(1)),
            "raw_score": int(m.group(2)),
            "center_score": int(m.group(3)),
            "threshold": int(m.group(4)),
            "center_threshold": int(m.group(5)),
            "best_template": int(m.group(6)),
            "total": int(m.group(7)),
            "dx": int(m.group(8)),
            "dy": int(m.group(9)),
        }

    match = None
    for m in RE_RESULT.finditer(text):
        match = m.group(1) == "verify-match"

    return {"templates": templates, "best": best, "match": match}


# ============ 功能 1: 录入模板 ============
def enroll_template():
    """录入 7 张模板指纹"""
    ensure_fprintd()

    print("\n" + "=" * 60)
    print("录入模板指纹 (7 张)")
    print("=" * 60)

    run_cmd(f"sudo -n {FPRINTD_DELETE} {USER}", timeout=30)
    output = run_cmd(f"sudo -n {FPRINTD_ENROLL} -f {FINGER} {USER}", timeout=300)
    print(output)

    if "enroll-completed" in output:
        print("\n✓ 模板录入成功!")
        return True
    print("\n✗ 模板录入失败!")
    return False


# ============ 功能 2: 录制样本 ============
def capture_samples(sample_type):
    """
    录制指纹样本集
    通过 fprintd-enroll 采集，每次 enroll 保存第 1 张图像
    """
    ensure_fprintd()
    type_name = "正确手指" if sample_type == "genuine" else "错误手指"

    print("\n" + "=" * 60)
    print(f"录制 {type_name} 样本集")
    print("=" * 60)
    print("每次 enroll 需要放置手指 7 次")
    print("按 Ctrl+C 停止录制")
    print()

    sample_dir = os.path.join(DATA_DIR, sample_type)
    os.makedirs(sample_dir, exist_ok=True)

    # 备份模板
    backup = None
    try:
        with open(TEMPLATE_FILE, "rb") as f:
            backup = f.read()
    except FileNotFoundError:
        print("错误: 模板不存在，请先录入模板")
        return

    count = 0
    samples = []

    try:
        while True:
            count += 1
            print(f"\n--- 样本 #{count} ---")

            # 删除 + 录入
            run_cmd(f"sudo -n {FPRINTD_DELETE} {USER}", timeout=30)
            output = run_cmd(f"sudo -n {FPRINTD_ENROLL} -f {FINGER} {USER}", timeout=300)

            if "enroll-completed" not in output:
                print("  录入失败，跳过")
                count -= 1
                continue

            # 读取第 1 张图像
            try:
                with open(TEMPLATE_FILE, "rb") as f:
                    data = f.read()

                if len(data) >= 16 + IMAGE_SIZE and data[:4] == b'F9RM':
                    img = data[16:16 + IMAGE_SIZE]

                    # 保存
                    sid = f"{sample_type}_{count:04d}"
                    with open(os.path.join(sample_dir, f"{sid}.bin"), "wb") as f:
                        f.write(img)

                    quality = analyze_quality(img)
                    with open(os.path.join(sample_dir, f"{sid}.json"), "w") as f:
                        json.dump({"id": sid, "type": sample_type,
                                   "timestamp": datetime.now().isoformat(),
                                   "quality": quality}, f, indent=2)

                    samples.append(sid)
                    print(f"  ✓ {sid}: stddev={quality['stddev']} contrast={quality['contrast']}")
                else:
                    print("  模板无效，跳过")
                    count -= 1

            except Exception as e:
                print(f"  保存失败: {e}")
                count -= 1

            time.sleep(1)

    except KeyboardInterrupt:
        print(f"\n\n录制停止，共 {len(samples)} 个样本")

    # 恢复模板
    if backup:
        with open(TEMPLATE_FILE, "wb") as f:
            f.write(backup)
        print("模板已恢复")

    # 保存索引
    with open(os.path.join(sample_dir, "index.json"), "w") as f:
        json.dump({"type": sample_type, "count": len(samples),
                   "samples": samples, "created": datetime.now().isoformat()}, f, indent=2)

    print(f"\n✓ 保存完成: {len(samples)} 个样本 → {sample_dir}")


def analyze_quality(data):
    """分析图像质量"""
    n = len(data)
    if n != IMAGE_SIZE:
        return {"mean": 0, "stddev": 0, "contrast": 0, "dark_pct": 0, "bright_pct": 0}

    mean = sum(data) / n
    var = sum((v - mean) ** 2 for v in data) / n
    return {
        "mean": round(mean), "stddev": round(var ** 0.5),
        "contrast": max(data) - min(data),
        "dark_pct": round(sum(1 for v in data if v <= 16) * 100 / n),
        "bright_pct": round(sum(1 for v in data if v >= 240) * 100 / n),
    }


# ============ 功能 3: 匹配计算 ============
def run_matching():
    """对所有样本执行匹配计算"""
    ensure_fprintd()

    print("\n" + "=" * 60)
    print("匹配计算")
    print("=" * 60)

    if not os.path.exists(TEMPLATE_FILE):
        print("错误: 模板不存在")
        return

    genuine_dir = os.path.join(DATA_DIR, "genuine")
    impostor_dir = os.path.join(DATA_DIR, "impostor")

    genuine = load_index(genuine_dir)
    impostor = load_index(impostor_dir)

    print(f"正确样本: {len(genuine)} 个")
    print(f"错误样本: {len(impostor)} 个\n")

    report = {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "timestamp": datetime.now().isoformat(),
        "genuine_results": [],
        "impostor_results": [],
    }

    # 正确样本
    if genuine:
        print("--- 正确样本匹配 ---")
        for i, sid in enumerate(genuine):
            sfile = os.path.join(genuine_dir, f"{sid}.bin")
            if not os.path.exists(sfile):
                continue
            print(f"  [{i+1}/{len(genuine)}] {sid}...", end=" ", flush=True)
            r = match_one(sfile)
            if r:
                r["sample_id"] = sid
                report["genuine_results"].append(r)
                print(f"score={r['score']} {'✓' if r['match'] else '✗'}")
            else:
                print("失败")

    # 错误样本
    if impostor:
        print("\n--- 错误样本匹配 ---")
        for i, sid in enumerate(impostor):
            sfile = os.path.join(impostor_dir, f"{sid}.bin")
            if not os.path.exists(sfile):
                continue
            print(f"  [{i+1}/{len(impostor)}] {sid}...", end=" ", flush=True)
            r = match_one(sfile)
            if r:
                r["sample_id"] = sid
                report["impostor_results"].append(r)
                print(f"score={r['score']} {'✓' if r['match'] else '✗'}")
            else:
                print("失败")

    # 统计
    report["stats"] = calc_stats(report)

    # 保存
    rdir = os.path.join(DATA_DIR, "reports")
    os.makedirs(rdir, exist_ok=True)
    rfile = os.path.join(rdir, f"report_{report['id']}.json")
    with open(rfile, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print_summary(report, rfile)
    return report


def load_index(d):
    f = os.path.join(d, "index.json")
    if not os.path.exists(f):
        return []
    return json.load(open(f)).get("samples", [])


def match_one(sample_file):
    """单个样本 vs 模板 → 分数"""
    # 备份 + 临时 enroll
    backup = None
    try:
        backup = open(TEMPLATE_FILE, "rb").read()
    except:
        pass

    try:
        img = open(sample_file, "rb").read()
        if len(img) != IMAGE_SIZE:
            return None

        # 创建单样本模板
        hdr = b'F9RM' + bytes([1, 16, 1]) + b'\x00' * 10
        with open(TEMPLATE_FILE, "wb") as f:
            f.write(hdr + img)

        # verify
        output = run_cmd(f"sudo -n {FPRINTD_VERIFY} -f {FINGER} {USER}", timeout=60)
        parsed = parse_verify_output(output)

        if parsed["best"]:
            return {
                "score": parsed["best"]["score"],
                "raw_score": parsed["best"]["raw_score"],
                "center_score": parsed["best"]["center_score"],
                "template": parsed["best"]["best_template"],
                "match": parsed["match"],
            }
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if backup:
            try:
                open(TEMPLATE_FILE, "wb").write(backup)
            except:
                pass
    return None


def calc_stats(report):
    """计算统计"""
    g = [r["score"] for r in report["genuine_results"]]
    b = [r["score"] for r in report["impostor_results"]]
    gm = sum(1 for r in report["genuine_results"] if r["match"])
    bm = sum(1 for r in report["impostor_results"] if r["match"])
    return {
        "genuine_count": len(g), "genuine_match": gm,
        "genuine_rate": gm / len(g) if g else 0,
        "genuine_min": min(g) if g else 0,
        "genuine_max": max(g) if g else 0,
        "genuine_mean": sum(g) / len(g) if g else 0,
        "impostor_count": len(b), "impostor_false_match": bm,
        "impostor_far": bm / len(b) if b else 0,
        "impostor_min": min(b) if b else 0,
        "impostor_max": max(b) if b else 0,
        "impostor_mean": sum(b) / len(b) if b else 0,
    }


def print_summary(rp, rfile):
    s = rp["stats"]
    print("\n" + "=" * 60)
    print("匹配报告")
    print("=" * 60)
    print(f"\n正确样本: {s['genuine_count']} 个, 匹配 {s['genuine_match']} ({s['genuine_rate']*100:.1f}%)")
    if s['genuine_count']:
        print(f"  分数: {s['genuine_min']}-{s['genuine_max']} 均值={s['genuine_mean']:.1f}")
    print(f"错误样本: {s['impostor_count']} 个, 误匹配 {s['impostor_false_match']} (FAR={s['impostor_far']*100:.1f}%)")
    if s['impostor_count']:
        print(f"  分数: {s['impostor_min']}-{s['impostor_max']} 均值={s['impostor_mean']:.1f}")

    if rp["genuine_results"]:
        print(f"\n正确样本明细:")
        for x in rp["genuine_results"]:
            print(f"  {x['sample_id']}: {x['score']} ({'✓' if x['match'] else '✗'})")

    if rp["impostor_results"]:
        print(f"\n错误样本明细:")
        for x in rp["impostor_results"]:
            print(f"  {x['sample_id']}: {x['score']} ({'✓(!)' if x['match'] else '✗'})")

    print(f"\n报告: {rfile}")


def list_samples():
    print("\n数据集列表:")
    for t in ["genuine", "impostor"]:
        d = os.path.join(DATA_DIR, t)
        idx = os.path.join(d, "index.json")
        if not os.path.exists(idx):
            print(f"\n{t}: (空)")
            continue
        data = json.load(open(idx))
        print(f"\n{t}: {data['count']} 个")
        for s in data["samples"][:5]:
            m = json.load(open(os.path.join(d, f"{s}.json")))
            q = m.get("quality", {})
            print(f"  {s}: stddev={q.get('stddev','?')} contrast={q.get('contrast','?')}")


def list_reports():
    d = os.path.join(DATA_DIR, "reports")
    if not os.path.exists(d):
        print("没有报告")
        return
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        data = json.load(open(os.path.join(d, f)))
        s = data.get("stats", {})
        print(f"\n{f}:")
        print(f"  正确: {s.get('genuine_count',0)} 匹配率 {s.get('genuine_rate',0)*100:.1f}%")
        print(f"  错误: {s.get('impostor_count',0)} FAR {s.get('impostor_far',0)*100:.1f}%")


def analyze_file(path):
    analyze = json.load(open(path))
    print(f"\n分析报告: {path}")
    samples = analyze.get("samples", [])
    if not samples:
        print("无数据")
        return
    gen = [s for s in samples if s.get("type") == "genuine"]
    imp = [s for s in samples if s.get("type") == "impostor"]
    print(f"正确: {len(gen)}  错误: {len(imp)}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    os.makedirs(DATA_DIR, exist_ok=True)

    if cmd == "--enroll-template":
        enroll_template()
    elif cmd == "--capture":
        capture_samples(sys.argv[2] if len(sys.argv) > 2 else "genuine")
    elif cmd == "--match":
        run_matching()
    elif cmd == "--list":
        list_samples()
    elif cmd == "--report":
        list_reports()
    elif cmd == "--analyze":
        analyze_file(sys.argv[2] if len(sys.argv) > 2 else "")
    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
