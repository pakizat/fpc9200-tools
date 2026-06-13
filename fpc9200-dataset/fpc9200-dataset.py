#!/usr/bin/env python3
"""
FPC 9200 指纹数据采集与分析工具
================================

架构:
  - 数据采集: 通过 fprintd-enroll 采集指纹图像
  - 匹配算法: Python 独立实现（可迭代优化）
  - GUI: Electron + TypeScript
  - CLI: Python 脚本（供 AI 调用）

流程:
  1. 录入模板指纹 (enroll) → 保存为 template.bin
  2. 录制正确手指样本 → 每次 enroll 保存第 1 张 → genuine_001.bin, ...
  3. 录制错误手指样本 → 同上 → impostor_001.bin, ...
  4. 匹配计算 → 所有样本 vs template.bin → 分数报告
  5. 分析分数分布 → 优化算法参数 → 重新匹配
"""

import subprocess
import json
import os
import sys
import time
import struct
from pathlib import Path

# ============ 配置 ============
DATA_DIR = Path.home() / "projects/fingerprint-driver/data/fpc9200-dataset"
TEMPLATE_FILE = Path("/var/lib/fprint/skyworker/fpcmoc/0/7")
FPRINTD_ENROLL = "/home/skyworker/fprintd/build/utils/fprintd-enroll"
FPRINTD_VERIFY = "/home/skyworker/fprintd/build/utils/fprintd-verify"
FPRINTD_DELETE = "/home/skyworker/fprintd/build/utils/fprintd-delete"
USER = "skyworker"
FINGER = "right-index-finger"
IMAGE_SIZE = 112 * 88  # 9856


# ============ 数据采集 ============

def enroll_template():
    """录入模板指纹（调用系统 enroll，7 张）"""
    print("录入模板指纹（7 次按压）...")
    run(f"sudo {FPRINTD_DELETE} {USER}")
    output = run(f"sudo {FPRINTD_ENROLL} -f {FINGER} {USER}", timeout=300)
    return "enroll-completed" in output


def capture_sample(label: str) -> bytes | None:
    """
    采集单张指纹样本
    通过 enroll 流程采集，保存第 1 张图像
    返回: 9856 字节灰度图像数据，失败返回 None
    """
    # 删除旧模板
    run(f"sudo {FPRINTD_DELETE} {USER}", timeout=30)

    # 录入（需要 7 次按压，但我们只取第 1 张）
    output = run(f"sudo {FPRINTD_ENROLL} -f {FINGER} {USER}", timeout=300)

    if "enroll-completed" not in output:
        print(f"  ✗ 录入失败")
        return None

    # 读取模板文件中的第 1 张图像
    if not TEMPLATE_FILE.exists():
        print(f"  ✤ 模板文件不存在: {TEMPLATE_FILE}")
        return None

    data = TEMPLATE_FILE.read_bytes()
    if len(data) < 16 + IMAGE_SIZE or data[:4] != b'F9RM':
        print(f"  ✗ 模板文件格式错误")
        return None

    image = data[16:16 + IMAGE_SIZE]
    print(f"  ✓ 采集成功 ({len(image)} bytes)")
    return image


def capture_samples_interactive(sample_type: str):
    """
    交互式录制样本集
    用户每按一次采集一个样本，输入 q 退出
    """
    type_name = "正确手指" if sample_type == "genuine" else "错误手指"
    print(f"\n录制 {type_name} 样本集")
    print("=" * 40)
    print("命令: [Enter]=采集  [q]=退出")
    print()

    sample_dir = DATA_DIR / sample_type
    sample_dir.mkdir(parents=True, exist_ok=True)

    # 读取当前索引
    existing = list(sample_dir.glob("*.bin"))
    count = len(existing)
    samples = []

    while True:
        cmd = input(f"样本 #{count+1} > ").strip()
        if cmd.lower() == 'q':
            break

        count += 1
        sample_id = f"{sample_type}_{count:04d}"

        print(f"  采集 {sample_id}...")
        image = capture_sample(sample_id)

        if image is None:
            count -= 1
            continue

        # 保存
        (sample_dir / f"{sample_id}.bin").write_bytes(image)

        # 质量分析
        quality = analyze_quality(image)
        meta = {
            "id": sample_id,
            "type": sample_type,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "quality": quality,
        }
        (sample_dir / f"{sample_id}.json").write_text(json.dumps(meta, indent=2))

        samples.append(sample_id)
        print(f"  质量: stddev={quality['stddev']} contrast={quality['contrast']}")

    print(f"\n共采集 {len(samples)} 个样本 → {sample_dir}")
    return samples


# ============ 图像质量分析 ============

def analyze_quality(image: bytes) -> dict:
    """分析指纹图像质量"""
    n = len(image)
    if n != IMAGE_SIZE:
        return {"mean": 0, "stddev": 0, "contrast": 0, "dark_pct": 0, "bright_pct": 0}

    values = list(image)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n

    return {
        "mean": round(mean),
        "stddev": round(var ** 0.5),
        "contrast": max(values) - min(values),
        "dark_pct": round(sum(1 for v in values if v <= 16) * 100 / n),
        "bright_pct": round(sum(1 for v in values if v >= 240) * 100 / n),
    }


# ============ 匹配算法 ============

def match_image(templ: bytes, probe: bytes, search_radius: int = 24) -> dict:
    """
    NCC 匹配 + 位移搜索
    返回: {score, raw_score, center_score, dx, dy, weighted_score, quality_weight}
    """
    edge = 8
    n = 0
    sum_a = sum_b = sum_aa = sum_bb = sum_ab = 0

    best_score = -999999
    best_raw = -999999
    best_dx = 0
    best_dy = 0

    for dy in range(-search_radius, search_radius + 1):
        for dx in range(-search_radius, search_radius + 1):
            # 计算 NCC
            s_a = s_b = s_aa = s_bb = s_ab = 0
            nn = 0

            for y in range(edge, IMAGE_HEIGHT - edge):
                yy = y + dy
                if yy < edge or yy >= IMAGE_HEIGHT - edge:
                    continue
                for x in range(edge, IMAGE_WIDTH - edge):
                    xx = x + dx
                    if xx < edge or xx >= IMAGE_WIDTH - edge:
                        continue
                    a = templ[y * IMAGE_WIDTH + x]
                    b = probe[yy * IMAGE_WIDTH + xx]
                    s_a += a; s_b += b
                    s_aa += a * a; s_bb += b * b
                    s_ab += a * b
                    nn += 1

            if nn < 100:
                continue

            cov = s_ab - (s_a * s_b) / nn
            var_a = s_aa - (s_a * s_a) / nn
            var_b = s_bb - (s_b * s_b) / nn

            if var_a <= 1.0 or var_b <= 1.0:
                continue

            raw = (1000.0 * cov) / (var_a * var_b) ** 0.5
            offset = abs(dx) + abs(dy)
            score = raw - offset * OFFSET_PENALTY

            if score > best_score:
                best_score = score
                best_raw = raw
                best_dx = dx
                best_dy = dy

    # 中心区域评分
    x0, y0 = IMAGE_WIDTH // 4, IMAGE_HEIGHT // 4
    x1, y1 = (IMAGE_WIDTH * 3) // 4, (IMAGE_HEIGHT * 3) // 4
    s_a = s_b = s_aa = s_bb = s_ab = 0
    nn = 0

    for y in range(y0, y1):
        yy = y + best_dy
        if yy < edge or yy >= IMAGE_HEIGHT - edge:
            continue
        for x in range(x0, x1):
            xx = x + best_dx
            if xx < edge or xx >= IMAGE_WIDTH - edge:
                continue
            a = templ[y * IMAGE_WIDTH + x]
            b = probe[yy * IMAGE_WIDTH + xx]
            s_a += a; s_b += b
            s_aa += a * a; s_bb += b * b
            s_ab += a * b
            nn += 1

    center_score = 0
    if nn >= 100:
        cov = s_ab - (s_a * s_b) / nn
        var_a = s_aa - (s_a * s_a) / nn
        var_b = s_bb - (s_b * s_b) / nn
        if var_a > 1.0 and var_b > 1.0:
            center_score = round((1000.0 * cov) / (var_a * var_b) ** 0.5)

    # 质量权重
    q = analyze_quality(probe)
    w = compute_quality_weight(q)

    return {
        "score": round(best_score if best_score > -999999 else 0),
        "raw_score": round(best_raw if best_raw > -999999 else 0),
        "center_score": center_score,
        "dx": best_dx,
        "dy": best_dy,
        "weighted_score": round((best_score if best_score > -999999 else 0) * w),
        "quality_weight": round(w, 3),
    }


def compute_quality_weight(q: dict) -> float:
    """计算质量权重"""
    w = q["stddev"] / 30.0
    w = max(0.3, min(1.0, w))

    c = q["contrast"] / 128.0
    w *= max(0.3, min(1.0, c))

    d = 1.0 - q["dark_pct"] / 50.0
    w *= max(0.3, min(1.0, d))

    b = 1.0 - q["bright_pct"] / 40.0
    w *= max(0.3, min(1.0, b))

    return w


# ============ 批量匹配与报告 ============

def run_matching():
    """对所有样本执行匹配计算，输出报告"""
    print("\n" + "=" * 60)
    print("匹配计算")
    print("=" * 60)

    # 加载模板
    if not TEMPLATE_FILE.exists():
        print("错误: 模板不存在，请先录入模板")
        return

    template_data = TEMPLATE_FILE.read_bytes()
    if len(template_data) < 16 + IMAGE_SIZE or template_data[:4] != b'F9RM':
        print("错误: 模板文件格式错误")
        return

    # 提取 7 张子模板
    templates = []
    count = template_data[6]
    for i in range(count):
        offset = 16 + i * IMAGE_SIZE
        templates.append(template_data[offset:offset + IMAGE_SIZE])

    print(f"模板: {count} 张子模板")

    # 加载样本
    genuine_dir = DATA_DIR / "genuine"
    impostor_dir = DATA_DIR / "impostor"

    genuine_samples = sorted(genuine_dir.glob("*.bin")) if genuine_dir.exists() else []
    impostor_samples = sorted(impostor_dir.glob("*.bin")) if impostor_dir.exists() else []

    print(f"正确样本: {len(genuine_samples)} 个")
    print(f"错误样本: {len(impostor_samples)} 个\n")

    if not genuine_samples and not impostor_samples:
        print("没有样本，请先录制样本")
        return

    # 匹配
    genuine_results = []
    impostor_results = []
    score_threshold = 200
    center_threshold = 350

    if genuine_samples:
        print("--- 正确样本匹配 ---")
        for i, f in enumerate(genuine_samples):
            probe = f.read_bytes()
            if len(probe) != IMAGE_SIZE:
                continue

            # vs 每张子模板，取最佳
            best = {"score": -999999, "template": 0}
            for ti, tmpl in enumerate(templates):
                r = match_image(tmpl, probe)
                if r["score"] > best["score"]:
                    best = {**r, "template": ti}

            matched = best["score"] >= score_threshold or best.get("center_score", 0) >= center_threshold

            result = {
                "sample_id": f.stem,
                "score": best["score"],
                "raw_score": best.get("raw_score", 0),
                "center_score": best.get("center_score", 0),
                "template": best["template"],
                "matched": matched,
            }
            genuine_results.append(result)
            print(f"  {f.stem}: score={best['score']} template={best['template']} {'✓' if matched else '✗'}")

    if impostor_samples:
        print("\n--- 错误样本匹配 ---")
        for i, f in enumerate(impostor_samples):
            probe = f.read_bytes()
            if len(probe) != IMAGE_SIZE:
                continue

            best = {"score": -999999, "template": 0}
            for ti, tmpl in enumerate(templates):
                r = match_image(tmpl, probe)
                if r["score"] > best["score"]:
                    best = {**r, "template": ti}

            matched = best["score"] >= score_threshold or best.get("center_score", 0) >= center_threshold

            result = {
                "sample_id": f.stem,
                "score": best["score"],
                "raw_score": best.get("raw_score", 0),
                "center_score": best.get("center_score", 0),
                "template": best["template"],
                "matched": matched,
            }
            impostor_results.append(result)
            print(f"  {f.stem}: score={best['score']} template={best['template']} {'✓(!)' if matched else '✗'}")

    # 统计
    gen_scores = [r["score"] for r in genuine_results]
    imp_scores = [r["score"] for r in impostor_results]
    gen_match = sum(1 for r in genuine_results if r["matched"])
    imp_match = sum(1 for r in impostor_results if r["matched"])

    stats = {
        "genuine_count": len(genuine_results),
        "genuine_match": gen_match,
        "genuine_rate": gen_match / len(genuine_results) if genuine_results else 0,
        "genuine_min": min(gen_scores) if gen_scores else 0,
        "genuine_max": max(gen_scores) if gen_scores else 0,
        "genuine_mean": sum(gen_scores) / len(gen_scores) if gen_scores else 0,
        "impostor_count": len(impostor_results),
        "impostor_false_match": imp_match,
        "impostor_far": imp_match / len(impostor_results) if impostor_results else 0,
        "impostor_min": min(imp_scores) if imp_scores else 0,
        "impostor_max": max(imp_scores) if imp_scores else 0,
        "impostor_mean": sum(imp_scores) / len(imp_scores) if imp_scores else 0,
    }

    # ROC 曲线
    roc = []
    all_scores = sorted(set(gen_scores + imp_scores))
    for threshold in range(0, (max(all_scores) if all_scores else 500) + 50, 25):
        fa = sum(1 for s in imp_scores if s >= threshold)
        fr = sum(1 for s in gen_scores if s < threshold)
        far = (fa / len(imp_scores) * 100) if imp_scores else 0
        frr = (fr / len(gen_scores) * 100) if gen_scores else 0
        total = len(gen_scores) + len(imp_scores)
        correct = (len(gen_scores) - fr) + (len(imp_scores) - fa)
        acc = (correct / total * 100) if total else 0
        roc.append({"threshold": threshold, "far": round(far, 1), "frr": round(frr, 1), "accuracy": round(acc, 1)})

    # 报告
    report = {
        "id": time.strftime("%Y%m%d_%H%M%S"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "score_threshold": score_threshold,
        "center_threshold": center_threshold,
        "genuine_results": genuine_results,
        "impostor_results": impostor_results,
        "stats": stats,
        "roc": roc,
    }

    # 保存
    report_dir = DATA_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"report_{report['id']}.json"
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    # 输出摘要
    print("\n" + "=" * 60)
    print("匹配报告摘要")
    print("=" * 60)
    print(f"\n正确样本: {stats['genuine_count']} 个")
    print(f"  匹配: {stats['genuine_match']} ({stats['genuine_rate']*100:.1f}%)")
    if gen_scores:
        print(f"  分数: {stats['genuine_min']}-{stats['genuine_max']} 均值={stats['genuine_mean']:.1f}")

    print(f"\n错误样本: {stats['impostor_count']} 个")
    print(f"  误匹配: {stats['impostor_false_match']} (FAR={stats['impostor_far']*100:.1f}%)")
    if imp_scores:
        print(f"  分数: {stats['impostor_min']}-{stats['impostor_max']} 均值={stats['impostor_mean']:.1f}")

    print(f"\n报告: {report_file}")
    return report


# ============ 工具函数 ============

def run(cmd, timeout=300):
    """运行命令"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return ""


def list_samples():
    """列出样本"""
    print("\n数据集列表:\n")
    for t in ["genuine", "impostor"]:
        d = DATA_DIR / t
        files = sorted(d.glob("*.bin")) if d.exists() else []
        print(f"{t}: {len(files)} 个样本")
        for f in files[:5]:
            meta = json.loads((d / f"{f.stem}.json").read_text()) if (d / f"{f.stem}.json").exists() else {}
            q = meta.get("quality", {})
            print(f"  {f.stem}: stddev={q.get('stddev','?')} contrast={q.get('contrast','?')}")


def list_reports():
    """列出报告"""
    d = DATA_DIR / "reports"
    if not d.exists():
        print("没有报告")
        return
    for f in sorted(d.glob("*.json")):
        r = json.loads(f.read_text())
        s = r.get("stats", {})
        print(f"\n{f.name}:")
        print(f"  正确: {s.get('genuine_count',0)} 匹配率 {s.get('genuine_rate',0)*100:.1f}%")
        print(f"  错误: {s.get('impostor_count',0)} FAR {s.get('impostor_far',0)*100:.1f}%")


def show_report(path: str):
    """显示报告详情"""
    r = json.loads(Path(path).read_text())
    s = r.get("stats", {})

    print(f"\n{'='*60}")
    print(f"报告: {r['id']}")
    print(f"{'='*60}")

    # ROC 表
    print(f"\n{'Threshold':>10} {'FAR%':>8} {'FRR%':>8} {'Acc%':>8}")
    print("-" * 40)
    for p in r.get("roc", []):
        print(f"{p['threshold']:>10} {p['far']:>8.1f} {p['frr']:>8.1f} {p['accuracy']:>8.1f}")

    # 最佳 operating point
    best = None
    for p in r.get("roc", []):
        if p["far"] <= 0.1 and p["frr"] <= 5.0:
            if best is None or p["accuracy"] > best["accuracy"]:
                best = p
    if best:
        print(f"\n推荐阈值: {best['threshold']} (FAR={best['far']:.1f}%, FRR={best['frr']:.1f}%, Acc={best['accuracy']:.1f}%)")


# ============ CLI 入口 ============

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n命令:")
        print("  --enroll-template    录入模板指纹")
        print("  --capture genuine    录制正确手指样本")
        print("  --capture impostor   录制错误手指样本")
        print("  --match              匹配计算")
        print("  --list               查看样本列表")
        print("  --reports            查看报告列表")
        print("  --report <file>      显示报告详情")
        sys.exit(0)

    cmd = sys.argv[1]
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if cmd == "--enroll-template":
        enroll_template()

    elif cmd == "--capture":
        t = sys.argv[2] if len(sys.argv) > 2 else "genuine"
        capture_samples_interactive(t)

    elif cmd == "--match":
        run_matching()

    elif cmd == "--list":
        list_samples()

    elif cmd == "--reports":
        list_reports()

    elif cmd == "--report":
        show_report(sys.argv[2] if len(sys.argv) > 2 else "")

    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
