#!/usr/bin/env python3
"""
FPC 9200 数据集收集工具
=======================

功能:
  1. 录入模板指纹 (enroll)
  2. 录制正确手指样本集 (每按一次保存一个样本)
  3. 录制错误手指样本集 (每按一次保存一个样本)
  4. 匹配计算 (所有样本 vs 模板，输出分数报告)

用法:
  python3 fpc9200-dataset.py --enroll-template     # 录入模板
  python3 fpc9200-dataset.py --capture genuine      # 开始录制正确手指样本
  python3 fpc9200-dataset.py --capture impostor     # 开始录制错误手指样本
  python3 fpc9200-dataset.py --match                # 匹配计算并输出报告
  python3 fpc9200-dataset.py --list                 # 查看样本列表
  python3 fpc9200-dataset.py --report               # 查看最新报告
"""

import subprocess
import json
import os
import sys
import time
import re
from datetime import datetime
from pathlib import Path

# ============ 配置 ============
DATA_DIR = os.path.expanduser("~/projects/fingerprint-driver/data/fpc9200-dataset")
CAPTURE_TOOL = os.path.expanduser("~/projects/fingerprint-driver/tools/fpc9200-capture/fpc9200-capture")
TEMPLATE_FILE = "/var/lib/fprint/skyworker/fpcmoc/0/7"
FPRINTD_ENROLL = "/home/skyworker/fprintd/build/utils/fprintd-enroll"
FPRINTD_VERIFY = "/home/skyworker/fprintd/build/utils/fprintd-verify"
FPRINTD_DELETE = "/home/skyworker/fprintd/build/utils/fprintd-delete"
USER = "skyworker"
FINGER = "right-index-finger"
IMAGE_SIZE = 112 * 88  # 9856 bytes

# ============ 日志解析 ============
RE_TEMPLATE_SCORE = re.compile(
    r'10a5:9200 RAW template\[(\d+)/(\d+)\] '
    r'score=(\d+) raw=(\d+) center=(\d+) '
    r'offset=(-?\d+),(-?\d+) '
    r'quality\(mean=(\d+) stddev=(\d+) contrast=(\d+) edge=(\d+) '
    r'dark=(\d+)% bright=(\d+)%'
)
RE_MATCH_SCORE = re.compile(
    r'10a5:9200 RAW match score: (\d+) raw=(\d+) center=(\d+) '
    r'\(threshold (\d+), center-threshold (\d+), '
    r'template (\d+)/(\d+), offset (-?\d+),(-?\d+)'
)
RE_VERIFY_RESULT = re.compile(r'Verify result: (verify-match|verify-no-match)')


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


# ============ 功能 1: 录入模板 ============
def enroll_template():
    """录入 7 张模板指纹"""
    ensure_fprintd()

    print("\n" + "=" * 60)
    print("录入模板指纹")
    print("=" * 60)
    print("请按提示放置手指 7 次，每次移开后重新放置到不同位置")
    print()

    # 删除旧模板
    run_cmd(f"sudo -n {FPRINTD_DELETE} {USER}", timeout=30)

    # 录入
    output = run_cmd(f"sudo -n {FPRINTD_ENROLL} -f {FINGER} {USER}", timeout=300)
    print(output)

    if "enroll-completed" in output:
        print("\n✓ 模板录入成功!")
        return True
    else:
        print("\n✗ 模板录入失败!")
        return False


# ============ 功能 2: 录制样本 ============
def capture_samples(sample_type):
    """
    录制指纹样本集
    sample_type: 'genuine' 或 'impostor'
    """
    type_name = "正确手指" if sample_type == "genuine" else "错误手指"
    print("\n" + "=" * 60)
    print(f"录制 {type_name} 样本集")
    print("=" * 60)
    print(f"请放置 {type_name}，每次按压会保存一个样本")
    print("按 Ctrl+C 停止录制")
    print()

    # 确保目录存在
    sample_dir = os.path.join(DATA_DIR, sample_type)
    os.makedirs(sample_dir, exist_ok=True)

    count = 0
    sample_ids = []

    try:
        while True:
            count += 1
            sample_id = f"{sample_type}_{count:04d}"
            output_file = os.path.join(sample_dir, f"{sample_id}.bin")

            print(f"\n--- 样本 #{count} ---")
            print("请放置手指...")

            # 调用 C 工具采集单张图像
            result = subprocess.run(
                ["sudo", "-n", CAPTURE_TOOL, output_file],
                capture_output=True, text=True, timeout=60
            )

            if result.returncode == 0 and os.path.exists(output_file):
                # 计算质量
                with open(output_file, "rb") as f:
                    image_data = f.read()

                quality = compute_quality(image_data)

                # 保存元数据
                meta = {
                    "id": sample_id,
                    "type": sample_type,
                    "timestamp": datetime.now().isoformat(),
                    "quality": quality,
                }
                meta_file = os.path.join(sample_dir, f"{sample_id}.json")
                with open(meta_file, "w") as f:
                    json.dump(meta, f, indent=2)

                sample_ids.append(sample_id)
                print(f"  ✓ 已保存: {sample_id} (质量: stddev={quality['stddev']}, contrast={quality['contrast']})")
            else:
                print(f"  ✗ 采集失败: {result.stderr[:200]}")
                count -= 1

            print("  移开手指，准备下一次...")
            time.sleep(1)

    except KeyboardInterrupt:
        print(f"\n\n录制停止，共采集 {len(sample_ids)} 个样本")

    # 保存样本索引
    index_file = os.path.join(sample_dir, "index.json")
    with open(index_file, "w") as f:
        json.dump({
            "type": sample_type,
            "count": len(sample_ids),
            "samples": sample_ids,
            "created": datetime.now().isoformat(),
        }, f, indent=2)

    print(f"\n✓ {type_name} 样本集保存完成: {len(sample_ids)} 个样本")
    print(f"  目录: {sample_dir}")


def compute_quality(image_data):
    """计算图像质量"""
    n = len(image_data)
    if n != IMAGE_SIZE:
        return {"mean": 0, "stddev": 0, "contrast": 0, "edge": 0, "dark_pct": 0, "bright_pct": 0}

    values = list(image_data)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    stddev = variance ** 0.5
    contrast = max(values) - min(values)
    dark = sum(1 for v in values if v <= 16)
    bright = sum(1 for v in values if v >= 240)

    return {
        "mean": round(mean),
        "stddev": round(stddev),
        "contrast": contrast,
        "edge": 0,
        "dark_pct": round(dark * 100 / n),
        "bright_pct": round(bright * 100 / n),
    }


# ============ 功能 3: 匹配计算 ============
def run_matching():
    """对所有样本执行匹配计算，输出报告"""
    ensure_fprintd()

    print("\n" + "=" * 60)
    print("匹配计算")
    print("=" * 60)

    # 检查模板
    if not os.path.exists(TEMPLATE_FILE):
        print("错误: 模板指纹不存在，请先录入模板")
        return

    # 加载样本索引
    genuine_dir = os.path.join(DATA_DIR, "genuine")
    impostor_dir = os.path.join(DATA_DIR, "impostor")

    genuine_index = load_index(genuine_dir)
    impostor_index = load_index(impostor_dir)

    if not genuine_index and not impostor_index:
        print("错误: 没有找到任何样本，请先录制样本")
        return

    print(f"正确样本: {len(genuine_index)} 个")
    print(f"错误样本: {len(impostor_index)} 个")
    print()

    # 加载模板数据
    template_data = load_template_data()
    if not template_data:
        print("错误: 无法加载模板")
        return

    # 对每个样本执行匹配
    report = {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "timestamp": datetime.now().isoformat(),
        "template_file": TEMPLATE_FILE,
        "genuine_results": [],
        "impostor_results": [],
    }

    # 正确样本
    if genuine_index:
        print("--- 正确样本匹配 ---")
        for i, sample_id in enumerate(genuine_index):
            sample_file = os.path.join(genuine_dir, f"{sample_id}.bin")
            if not os.path.exists(sample_file):
                continue

            print(f"  [{i+1}/{len(genuine_index)}] {sample_id}...", end=" ", flush=True)

            # 临时 enroll 这个样本
            result = match_sample_to_template(sample_file, template_data)
            if result:
                result["sample_id"] = sample_id
                result["type"] = "genuine"
                report["genuine_results"].append(result)
                print(f"score={result['score']} raw={result['raw_score']} center={result['center_score']} {'MATCH' if result['match'] else 'NO-MATCH'}")
            else:
                print("失败")

    # 错误样本
    if impostor_index:
        print("\n--- 错误样本匹配 ---")
        for i, sample_id in enumerate(impostor_index):
            sample_file = os.path.join(impostor_dir, f"{sample_id}.bin")
            if not os.path.exists(sample_file):
                continue

            print(f"  [{i+1}/{len(impostor_index)}] {sample_id}...", end=" ", flush=True)

            result = match_sample_to_template(sample_file, template_data)
            if result:
                result["sample_id"] = sample_id
                result["type"] = "impostor"
                report["impostor_results"].append(result)
                print(f"score={result['score']} raw={result['raw_score']} center={result['center_score']} {'MATCH' if result['match'] else 'NO-MATCH'}")
            else:
                print("失败")

    # 计算统计
    report["statistics"] = compute_statistics(report)

    # 保存报告
    report_dir = os.path.join(DATA_DIR, "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_file = os.path.join(report_dir, f"report_{report['id']}.json")
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # 输出摘要
    print_report_summary(report, report_file)

    return report


def load_index(directory):
    """加载样本索引"""
    index_file = os.path.join(directory, "index.json")
    if not os.path.exists(index_file):
        return []
    with open(index_file) as f:
        data = json.load(f)
    return data.get("samples", [])


def load_template_data():
    """加载模板数据"""
    try:
        with open(TEMPLATE_FILE, "rb") as f:
            data = f.read()
        if len(data) >= 16 and data[:4] == b'F9RM':
            count = data[6]
            return {"count": count, "data": data}
    except:
        pass
    return None


def match_sample_to_template(sample_file, template_data):
    """
    将样本临时 enroll 到系统，然后执行 verify 获取匹配分数
    """
    # 备份当前模板
    backup = None
    try:
        with open(TEMPLATE_FILE, "rb") as f:
            backup = f.read()
    except:
        pass

    try:
        # 读取样本图像
        with open(sample_file, "rb") as f:
            image_data = f.read()

        if len(image_data) != IMAGE_SIZE:
            return None

        # 创建单样本模板
        header = bytearray(16)
        header[0:4] = b'F9RM'
        header[4] = 1   # version
        header[5] = 16  # header_len
        header[6] = 1   # count
        temp_template = bytes(header) + image_data

        # 写入临时模板
        with open(TEMPLATE_FILE, "wb") as f:
            f.write(temp_template)

        # 执行 verify
        output = run_cmd(f"sudo -n {FPRINTD_VERIFY} -f {FINGER} {USER}", timeout=60)

        # 解析结果
        result = parse_verify_log(output)
        if result["best_match"]:
            return {
                "score": result["best_match"]["score"],
                "raw_score": result["best_match"]["raw_score"],
                "center_score": result["best_match"]["center_score"],
                "template": result["best_match"]["best_template"],
                "total": result["best_match"]["total_templates"],
                "match": result["match"],
            }

    except Exception as e:
        print(f"Error: {e}")
    finally:
        # 恢复模板
        if backup:
            try:
                with open(TEMPLATE_FILE, "wb") as f:
                    f.write(backup)
            except:
                pass

    return None


def parse_verify_log(log_text):
    """解析 verify 日志"""
    templates = []
    for m in RE_TEMPLATE_SCORE.finditer(log_text):
        templates.append({
            "template": int(m.group(1)),
            "score": int(m.group(3)),
            "raw_score": int(m.group(4)),
            "center_score": int(m.group(5)),
        })

    best_match = None
    for m in RE_MATCH_SCORE.finditer(log_text):
        best_match = {
            "score": int(m.group(1)),
            "raw_score": int(m.group(2)),
            "center_score": int(m.group(3)),
            "best_template": int(m.group(6)),
            "total_templates": int(m.group(7)),
        }

    match_result = None
    for m in RE_VERIFY_RESULT.finditer(log_text):
        match_result = m.group(1) == "verify-match"

    return {
        "templates": templates,
        "best_match": best_match,
        "match": match_result,
    }


def compute_statistics(report):
    """计算统计信息"""
    gen_scores = [r["score"] for r in report["genuine_results"]]
    imp_scores = [r["score"] for r in report["impostor_results"]]

    gen_match = sum(1 for r in report["genuine_results"] if r["match"])
    imp_match = sum(1 for r in report["impostor_results"] if r["match"])

    return {
        "genuine": {
            "count": len(report["genuine_results"]),
            "match_count": gen_match,
            "match_rate": gen_match / len(report["genuine_results"]) if report["genuine_results"] else 0,
            "min_score": min(gen_scores) if gen_scores else 0,
            "max_score": max(gen_scores) if gen_scores else 0,
            "mean_score": sum(gen_scores) / len(gen_scores) if gen_scores else 0,
            "scores": gen_scores,
        },
        "impostor": {
            "count": len(report["impostor_results"]),
            "false_match_count": imp_match,
            "false_match_rate": imp_match / len(report["impostor_results"]) if report["impostor_results"] else 0,
            "min_score": min(imp_scores) if imp_scores else 0,
            "max_score": max(imp_scores) if imp_scores else 0,
            "mean_score": sum(imp_scores) / len(imp_scores) if imp_scores else 0,
            "scores": imp_scores,
        }
    }


def print_report_summary(report, report_file):
    """输出报告摘要"""
    stats = report["statistics"]

    print("\n" + "=" * 60)
    print("匹配报告摘要")
    print("=" * 60)

    print(f"\n正确样本 (genuine):")
    print(f"  总数: {stats['genuine']['count']}")
    print(f"  匹配: {stats['genuine']['match_count']} ({stats['genuine']['match_rate']*100:.1f}%)")
    if stats['genuine']['scores']:
        print(f"  分数范围: {stats['genuine']['min_score']} - {stats['genuine']['max_score']}")
        print(f"  平均分: {stats['genuine']['mean_score']:.1f}")

    print(f"\n错误样本 (impostor):")
    print(f"  总数: {stats['impostor']['count']}")
    print(f"  误匹配: {stats['impostor']['false_match_count']} ({stats['impostor']['false_match_rate']*100:.1f}%)")
    if stats['impostor']['scores']:
        print(f"  分数范围: {stats['impostor']['min_score']} - {stats['impostor']['max_score']}")
        print(f"  平均分: {stats['impostor']['mean_score']:.1f}")

    # 详细列表
    if report["genuine_results"]:
        print(f"\n正确样本分数明细:")
        for r in report["genuine_results"]:
            print(f"  {r['sample_id']}: score={r['score']} raw={r['raw_score']} center={r['center_score']} {'✓' if r['match'] else '✗'}")

    if report["impostor_results"]:
        print(f"\n错误样本分数明细:")
        for r in report["impostor_results"]:
            print(f"  {r['sample_id']}: score={r['score']} raw={r['raw_score']} center={r['center_score']} {'✓(!)' if r['match'] else '✗'}")

    print(f"\n报告已保存: {report_file}")


# ============ 功能 4: 查看列表 ============
def list_samples():
    """查看样本列表"""
    print("\n" + "=" * 60)
    print("数据集列表")
    print("=" * 60)

    for sample_type in ["genuine", "impostor"]:
        sample_dir = os.path.join(DATA_DIR, sample_type)
        index_file = os.path.join(sample_dir, "index.json")

        type_name = "正确手指" if sample_type == "genuine" else "错误手指"
        print(f"\n{type_name} 样本集:")

        if not os.path.exists(index_file):
            print("  (空)")
            continue

        with open(index_file) as f:
            index = json.load(f)

        print(f"  样本数: {index['count']}")
        print(f"  创建时间: {index['created']}")

        # 显示前 10 个样本的质量
        for sid in index["samples"][:10]:
            meta_file = os.path.join(sample_dir, f"{sid}.json")
            if os.path.exists(meta_file):
                with open(meta_file) as f:
                    meta = json.load(f)
                q = meta.get("quality", {})
                print(f"    {sid}: stddev={q.get('stddev', '?')} contrast={q.get('contrast', '?')}")

        if index["count"] > 10:
            print(f"    ... 还有 {index['count'] - 10} 个样本")


def list_reports():
    """查看报告列表"""
    report_dir = os.path.join(DATA_DIR, "reports")
    if not os.path.exists(report_dir):
        print("没有报告")
        return

    reports = sorted([f for f in os.listdir(report_dir) if f.endswith(".json")])
    if not reports:
        print("没有报告")
        return

    print("\n报告列表:")
    for r in reports:
        filepath = os.path.join(report_dir, r)
        with open(filepath) as f:
            data = json.load(f)
        stats = data.get("statistics", {})
        gen = stats.get("genuine", {})
        imp = stats.get("impostor", {})
        print(f"  {r}:")
        print(f"    正确: {gen.get('count', 0)} 个, 匹配率 {gen.get('match_rate', 0)*100:.1f}%")
        print(f"    错误: {imp.get('count', 0)} 个, 误匹配率 {imp.get('false_match_rate', 0)*100:.1f}%")


# ============ 主入口 ============
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "--enroll-template":
        enroll_template()

    elif command == "--capture":
        if len(sys.argv) < 3:
            print("请指定样本类型: genuine 或 impostor")
            sys.exit(1)
        sample_type = sys.argv[2]
        if sample_type not in ("genuine", "impostor"):
            print("样本类型必须是 genuine 或 impostor")
            sys.exit(1)
        capture_samples(sample_type)

    elif command == "--match":
        run_matching()

    elif command == "--list":
        list_samples()

    elif command == "--report":
        list_reports()

    elif command == "--help":
        print(__doc__)

    else:
        print(f"未知命令: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
