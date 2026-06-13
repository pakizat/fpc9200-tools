#!/usr/bin/env python3
"""
FPC 9200 指纹数据采集与分析工具
================================

架构:
  - 模板录入: 通过 fprintd-enroll 录入并导出 F9RM 模板副本
  - 数据采集: 通过 fpc9200-capture 单次采集 112x88 RAW 图像
  - 匹配算法: Python 独立实现（可迭代优化）
  - GUI: Electron + TypeScript
  - CLI: Python 脚本（供 AI 调用）

流程:
  1. 录入模板指纹 (enroll) → 导出 template.f9rm
  2. 录制正确手指样本 → genuine_001.bin, ...
  3. 录制错误手指样本 → impostor_001.bin, ...
  4. 匹配计算 → 所有样本 vs template.f9rm → 分数报告
  5. 分析分数分布 → 优化算法参数 → 重新匹配
"""

import subprocess
import json
import ctypes
import os
import sys
import time
import struct
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from gi.repository import GLib

# ============ 配置 ============
DATA_DIR = Path(os.environ.get(
    "FPC9200_DATA_DIR",
    str(Path.home() / "projects/fingerprint-driver/data/fpc9200-dataset"),
))
TEMPLATE_EXPORT = DATA_DIR / "template.f9rm"
TEMPLATE_FILE = Path("/var/lib/fprint/skyworker/fpcmoc/0/7")
FPRINTD_ENROLL = "/home/skyworker/fprintd/build/utils/fprintd-enroll"
FPRINTD_VERIFY = "/home/skyworker/fprintd/build/utils/fprintd-verify"
FPRINTD_DELETE = "/home/skyworker/fprintd/build/utils/fprintd-delete"
CAPTURE_TOOL = "/home/skyworker/projects/fingerprint-driver/tools/fpc9200-capture/fpc9200-capture"
USER = "skyworker"
FINGER = "right-index-finger"
IMAGE_WIDTH = 112
IMAGE_HEIGHT = 88
IMAGE_SIZE = IMAGE_WIDTH * IMAGE_HEIGHT  # 9856
F9RM_HEADER_SIZE = 16
OFFSET_PENALTY = 7
FP3_VARIANT_TYPE = GLib.VariantType.new("(issbymsmsia{sv}v)")
SCRIPT_DIR = Path(__file__).resolve().parent
MATCH_C = SCRIPT_DIR / "fpc9200_match.c"
MATCH_SO = SCRIPT_DIR / "fpc9200_match.so"
MATCH_LIB = None


class CMatchResult(ctypes.Structure):
    _fields_ = [
        ("score", ctypes.c_int),
        ("raw_score", ctypes.c_int),
        ("center_score", ctypes.c_int),
        ("dx", ctypes.c_int),
        ("dy", ctypes.c_int),
        ("edge_score", ctypes.c_int),
        ("block_mean", ctypes.c_int),
        ("block_median", ctypes.c_int),
        ("block_min", ctypes.c_int),
        ("block_top4", ctypes.c_int),
        ("block_good_250", ctypes.c_int),
        ("block_good_350", ctypes.c_int),
        ("block_good_450", ctypes.c_int),
    ]


def load_match_lib():
    global MATCH_LIB

    if MATCH_LIB is False:
        return None

    if MATCH_LIB is not None:
        return MATCH_LIB

    try:
        if (
            not MATCH_SO.exists() or
            (MATCH_C.exists() and MATCH_C.stat().st_mtime > MATCH_SO.stat().st_mtime)
        ):
            subprocess.run(
                [
                    "gcc",
                    "-O3",
                    "-fPIC",
                    "-shared",
                    "-o",
                    str(MATCH_SO),
                    str(MATCH_C),
                    "-lm",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

        lib = ctypes.CDLL(str(MATCH_SO))
        lib.fpc9200_match_image.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(CMatchResult),
        ]
        lib.fpc9200_match_image.restype = ctypes.c_int
        MATCH_LIB = lib
        return MATCH_LIB
    except Exception as e:
        print(f"警告: C 加速库不可用，回退 Python 匹配: {e}", flush=True)
        MATCH_LIB = False
        return None


# ============ 数据采集 ============

def enroll_template():
    """录入模板指纹，并导出 F9RM 模板副本"""
    print("录入模板指纹（7 次按压）...")
    run([FPRINTD_DELETE, USER], timeout=30)
    output = run([FPRINTD_ENROLL, "-f", FINGER, USER], timeout=300)
    if "enroll-completed" not in output:
        print("错误: 模板录入失败")
        print(output)
        return False

    template = load_template_from_system()
    if not template:
        print("错误: 无法从 fprintd 存储导出模板")
        return False

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATE_EXPORT.write_bytes(template)
    count, _, _, _ = parse_f9rm(template)
    print(f"✓ 模板已导出: {TEMPLATE_EXPORT} ({count} 张子模板)")
    return True


def capture_sample(label: str) -> bytes | None:
    """
    采集单张指纹样本
    直接调用 fpc9200-capture，不删除/覆盖系统模板
    返回: 9856 字节灰度图像数据，失败返回 None
    """
    if not Path(CAPTURE_TOOL).exists():
        print(f"  ✗ 采集工具不存在: {CAPTURE_TOOL}")
        return None

    tmp_dir = Path(tempfile.mkdtemp(prefix="fpc9200_capture_"))
    tmp_path = tmp_dir / f"{label}.bin"

    try:
        output = run(["sudo", "-n", CAPTURE_TOOL, str(tmp_path)], timeout=60)
        if not tmp_path.exists():
            print("  ✗ 采集失败: 未生成图像文件")
            print(output)
            return None

        image = tmp_path.read_bytes()
        if len(image) != IMAGE_SIZE:
            print(f"  ✗ 图像尺寸错误: {len(image)} != {IMAGE_SIZE}")
            print(output)
            return None

        print(f"  ✓ 采集成功 ({len(image)} bytes)")
        return image
    finally:
        tmp_path.unlink(missing_ok=True)
        tmp_dir.rmdir()


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


def capture_one_sample(sample_type: str) -> bool:
    """采集并保存单张样本，供 GUI/自动化调用"""
    sample_dir = DATA_DIR / sample_type
    sample_dir.mkdir(parents=True, exist_ok=True)

    count = len(list(sample_dir.glob("*.bin"))) + 1
    sample_id = f"{sample_type}_{count:04d}"

    print(f"采集 {sample_id}...")
    image = capture_sample(sample_id)
    if image is None:
        return False

    (sample_dir / f"{sample_id}.bin").write_bytes(image)
    quality = analyze_quality(image)
    meta = {
        "id": sample_id,
        "type": sample_type,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "quality": quality,
    }
    (sample_dir / f"{sample_id}.json").write_text(json.dumps(meta, indent=2))
    print(f"✓ 已保存: {sample_dir / f'{sample_id}.bin'}")
    print(f"质量: stddev={quality['stddev']} contrast={quality['contrast']}")
    return True


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


# ============ 模板解析 ============

def read_system_print() -> bytes | None:
    """读取 fprintd 存储中的 FP3 序列化模板"""
    try:
        return TEMPLATE_FILE.read_bytes()
    except PermissionError:
        pass
    except FileNotFoundError:
        print(f"错误: 系统模板不存在: {TEMPLATE_FILE}")
        return None

    try:
        r = subprocess.run(
            ["sudo", "-n", "/usr/bin/cat", str(TEMPLATE_FILE)],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception as e:
        print(f"错误: 读取系统模板失败: {e}")
        return None

    if r.returncode != 0:
        print("错误: 读取系统模板需要 sudo 免密权限")
        if r.stderr:
            print(r.stderr.decode(errors="replace"))
        return None

    return r.stdout


def extract_f9rm_from_fp3(data: bytes) -> bytes | None:
    """从 libfprint FP3 序列化 FpPrint 中提取 RAW fpi-data(F9RM)"""
    if len(data) > F9RM_HEADER_SIZE and data.startswith(b"F9RM"):
        parse_f9rm(data)
        return data

    if len(data) <= 3 or not data.startswith(b"FP3"):
        print("错误: 模板不是 FP3/F9RM 格式")
        return None

    try:
        raw = GLib.Bytes.new(data[3:])
        value = GLib.Variant.new_from_bytes(FP3_VARIANT_TYPE, raw, False)
        normal = value.get_normal_form()
        fpi_type = normal.get_child_value(0).get_int32()
        if fpi_type != 1:
            print(f"错误: 模板不是 RAW 类型，fpi-type={fpi_type}")
            return None

        variant_wrapper = normal.get_child_value(9)
        raw_variant = variant_wrapper.get_variant()
        if raw_variant.get_type_string() == "v":
            raw_variant = raw_variant.get_variant()
        if raw_variant.get_type_string() != "ay":
            print(f"错误: RAW 模板数据类型异常: {raw_variant.get_type_string()}")
            return None

        f9rm = raw_variant.get_data_as_bytes().get_data()
        parse_f9rm(f9rm)
        return f9rm
    except Exception as e:
        print(f"错误: 解析 FP3 模板失败: {e}")
        return None


def load_template_from_system() -> bytes | None:
    data = read_system_print()
    if not data:
        return None
    return extract_f9rm_from_fp3(data)


def load_template_for_matching() -> bytes | None:
    if TEMPLATE_EXPORT.exists():
        data = TEMPLATE_EXPORT.read_bytes()
        try:
            parse_f9rm(data)
            return data
        except ValueError as e:
            print(f"错误: 模板副本无效: {e}")
            return None

    print("未找到模板副本，尝试从系统 fprintd 存储导出...")
    template = load_template_from_system()
    if template:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TEMPLATE_EXPORT.write_bytes(template)
        print(f"✓ 模板已导出: {TEMPLATE_EXPORT}")
    return template


def parse_f9rm(data: bytes) -> tuple[int, int, int, bytes]:
    """返回 (count, width, height, image_data)"""
    if len(data) == IMAGE_SIZE:
        return 1, IMAGE_WIDTH, IMAGE_HEIGHT, data

    if len(data) < F9RM_HEADER_SIZE or data[:4] != b"F9RM":
        raise ValueError(f"invalid F9RM header/length: {len(data)}")

    version = data[4]
    header_len = data[5]
    width, height, count = struct.unpack_from("<HHH", data, 6)
    image_size = struct.unpack_from("<I", data, 12)[0]

    expected_len = header_len + count * image_size
    if (
        version != 1 or
        header_len != F9RM_HEADER_SIZE or
        width != IMAGE_WIDTH or
        height != IMAGE_HEIGHT or
        count <= 0 or
        image_size != IMAGE_SIZE or
        len(data) != expected_len
    ):
        raise ValueError(
            f"invalid F9RM metadata version={version} header={header_len} "
            f"width={width} height={height} count={count} size={image_size} len={len(data)}"
        )

    return count, width, height, data[header_len:]


# ============ 匹配算法 ============

def match_image(templ: bytes, probe: bytes, search_radius: int = 24) -> dict:
    """
    NCC 匹配 + 位移搜索
    返回: {score, raw_score, center_score, dx, dy, weighted_score, quality_weight}
    """
    lib = load_match_lib()
    if lib:
        c_result = CMatchResult()
        templ_buf = (ctypes.c_uint8 * len(templ)).from_buffer_copy(templ)
        probe_buf = (ctypes.c_uint8 * len(probe)).from_buffer_copy(probe)
        ret = lib.fpc9200_match_image(
            templ_buf,
            probe_buf,
            search_radius,
            OFFSET_PENALTY,
            ctypes.byref(c_result),
        )
        if ret == 0:
            q = analyze_quality(probe)
            w = compute_quality_weight(q)
            return {
                "score": c_result.score,
                "raw_score": c_result.raw_score,
                "center_score": c_result.center_score,
                "dx": c_result.dx,
                "dy": c_result.dy,
                "edge_score": c_result.edge_score,
                "block_mean": c_result.block_mean,
                "block_median": c_result.block_median,
                "block_min": c_result.block_min,
                "block_top4": c_result.block_top4,
                "block_good_250": c_result.block_good_250,
                "block_good_350": c_result.block_good_350,
                "block_good_450": c_result.block_good_450,
                "weighted_score": round(c_result.score * w),
                "quality_weight": round(w, 3),
            }

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


def match_sample_file(args: tuple[str, str, list[bytes], int, int]) -> tuple[str, dict | None, str | None]:
    """Worker: 匹配单个样本文件，返回 (sample_type, result, error)。"""
    sample_type, path_str, templates, score_threshold, center_threshold = args
    path = Path(path_str)
    probe = path.read_bytes()
    if len(probe) != IMAGE_SIZE:
        return sample_type, None, f"{path.name}: 图像尺寸错误 {len(probe)} != {IMAGE_SIZE}"

    best = {"score": -999999, "template": 0}
    candidates = []
    for ti, tmpl in enumerate(templates):
        r = match_image(tmpl, probe)
        candidates.append({**r, "template": ti})
        if r["score"] > best["score"]:
            best = {**r, "template": ti}

    candidates.sort(key=lambda r: r["score"], reverse=True)
    second_score = candidates[1]["score"] if len(candidates) > 1 else -999999
    score_gap = best["score"] - second_score
    offset_abs = abs(best.get("dx", 0)) + abs(best.get("dy", 0))

    matched = best["score"] >= score_threshold or best.get("center_score", 0) >= center_threshold
    v2 = classify_match_v2(best, score_gap, offset_abs)
    result = {
        "sample_id": path.stem,
        "score": best["score"],
        "raw_score": best.get("raw_score", 0),
        "center_score": best.get("center_score", 0),
        "edge_score": best.get("edge_score", 0),
        "block_mean": best.get("block_mean", 0),
        "block_median": best.get("block_median", 0),
        "block_min": best.get("block_min", 0),
        "block_top4": best.get("block_top4", 0),
        "block_good_250": best.get("block_good_250", 0),
        "block_good_350": best.get("block_good_350", 0),
        "block_good_450": best.get("block_good_450", 0),
        "weighted_score": best.get("weighted_score", 0),
        "quality_weight": best.get("quality_weight", 0),
        "dx": best.get("dx", 0),
        "dy": best.get("dy", 0),
        "offset_abs": offset_abs,
        "template": best["template"],
        "second_score": second_score,
        "score_gap": score_gap,
        "template_scores": candidates,
        "matched": matched,
        "v2_decision": v2["decision"],
        "v2_confidence": v2["confidence"],
        "v2_reason": v2["reason"],
        "v2_accept": v2["decision"] == "ACCEPT",
        "v2_retry": v2["decision"] == "RETRY",
    }
    return sample_type, result, None


def classify_match_v2(best: dict, score_gap: int, offset_abs: int) -> dict:
    """三段式验证判定：ACCEPT / RETRY / REJECT。"""
    score = best.get("score", 0)
    raw = best.get("raw_score", 0)
    center = best.get("center_score", 0)
    edge = best.get("edge_score", 0)
    block_mean = best.get("block_mean", 0)
    block_good = best.get("block_good_250", 0)
    quality = best.get("quality_weight", 1.0)

    if quality < 0.65:
        return {"decision": "RETRY", "confidence": 0, "reason": "low-quality"}

    strong_global = score >= 430 and raw >= 430 and center >= 350
    strong_blocks = block_mean >= 420 and block_good >= 10
    strong_edge = edge >= 220
    if strong_global and strong_blocks and (strong_edge or score_gap >= 60):
        return {"decision": "ACCEPT", "confidence": min(1000, score + center // 2), "reason": "strong-global-block"}

    clear_aligned = raw >= 425 and center >= 350 and edge >= 150 and block_mean >= 200
    if clear_aligned:
        return {"decision": "ACCEPT", "confidence": min(1000, raw + center // 3), "reason": "clear-aligned"}

    medium_global = score >= 260 and raw >= 350 and center >= 300
    medium_blocks = block_mean >= 260 and block_good >= 7
    if medium_global and medium_blocks and edge >= 120:
        return {"decision": "RETRY", "confidence": score + center // 3, "reason": "medium-match"}

    if score >= 180 or center >= 300 or raw >= 350:
        return {"decision": "RETRY", "confidence": max(score, center, raw), "reason": "ambiguous"}

    return {"decision": "REJECT", "confidence": max(score, center, raw), "reason": "below-threshold"}


# ============ 批量匹配与报告 ============

def run_matching():
    """对所有样本执行匹配计算，输出报告"""
    print("\n" + "=" * 60)
    print("匹配计算")
    print("=" * 60)

    # 加载模板副本
    template_data = load_template_for_matching()
    if not template_data:
        print("错误: 模板不存在，请先运行 --enroll-template 或 --export-template")
        return

    try:
        count, _, _, template_images = parse_f9rm(template_data)
    except ValueError as e:
        print(f"错误: 模板文件格式错误: {e}")
        return

    # 提取子模板
    templates = []
    for i in range(count):
        offset = i * IMAGE_SIZE
        templates.append(template_images[offset:offset + IMAGE_SIZE])

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
    load_match_lib()

    all_jobs = []
    for f in genuine_samples:
        all_jobs.append(("genuine", str(f), templates, score_threshold, center_threshold))
    for f in impostor_samples:
        all_jobs.append(("impostor", str(f), templates, score_threshold, center_threshold))

    workers = min(max(1, (os.cpu_count() or 2) - 1), len(all_jobs))
    print(f"并行 worker: {workers}，任务: {len(all_jobs)} 个样本 × {count} 张子模板", flush=True)

    completed = 0
    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(match_sample_file, job): job for job in all_jobs}
        for future in as_completed(future_map):
            sample_type, path_str, _, _, _ = future_map[future]
            completed += 1

            try:
                result_type, result, error = future.result()
            except Exception as e:
                print(f"  [{completed}/{len(all_jobs)}] {Path(path_str).stem}: 失败 {e}", flush=True)
                continue

            if error:
                print(f"  [{completed}/{len(all_jobs)}] {Path(path_str).stem}: {error}", flush=True)
                continue

            if result is None:
                continue

            if result_type == "genuine":
                genuine_results.append(result)
                mark = "✓" if result["matched"] else "✗"
                label = "正确"
            else:
                impostor_results.append(result)
                mark = "✓(!)" if result["matched"] else "✗"
                label = "错误"

            print(
                f"  [{completed}/{len(all_jobs)}] {label} {result['sample_id']}: "
                f"score={result['score']} raw={result['raw_score']} center={result['center_score']} "
                f"edge={result.get('edge_score', 0)} block={result.get('block_mean', 0)} "
                f"v2={result.get('v2_decision', '?')} template={result['template']} {mark}",
                flush=True,
            )

    genuine_results.sort(key=lambda r: r["sample_id"])
    impostor_results.sort(key=lambda r: r["sample_id"])

    # 统计
    gen_scores = [r["score"] for r in genuine_results]
    imp_scores = [r["score"] for r in impostor_results]
    gen_match = sum(1 for r in genuine_results if r["matched"])
    imp_match = sum(1 for r in impostor_results if r["matched"])
    gen_v2_accept = sum(1 for r in genuine_results if r.get("v2_decision") == "ACCEPT")
    gen_v2_retry = sum(1 for r in genuine_results if r.get("v2_decision") == "RETRY")
    gen_v2_reject = sum(1 for r in genuine_results if r.get("v2_decision") == "REJECT")
    imp_v2_accept = sum(1 for r in impostor_results if r.get("v2_decision") == "ACCEPT")
    imp_v2_retry = sum(1 for r in impostor_results if r.get("v2_decision") == "RETRY")
    imp_v2_reject = sum(1 for r in impostor_results if r.get("v2_decision") == "REJECT")

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
        "v2_genuine_accept": gen_v2_accept,
        "v2_genuine_retry": gen_v2_retry,
        "v2_genuine_reject": gen_v2_reject,
        "v2_genuine_accept_rate": gen_v2_accept / len(genuine_results) if genuine_results else 0,
        "v2_genuine_retry_rate": gen_v2_retry / len(genuine_results) if genuine_results else 0,
        "v2_genuine_reject_rate": gen_v2_reject / len(genuine_results) if genuine_results else 0,
        "v2_impostor_accept": imp_v2_accept,
        "v2_impostor_retry": imp_v2_retry,
        "v2_impostor_reject": imp_v2_reject,
        "v2_far": imp_v2_accept / len(impostor_results) if impostor_results else 0,
        "v2_impostor_retry_rate": imp_v2_retry / len(impostor_results) if impostor_results else 0,
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
        "template_file": str(TEMPLATE_EXPORT),
        "template_count": count,
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

    print("\nv2 三段式判定:")
    print(f"  正确样本 ACCEPT={gen_v2_accept} RETRY={gen_v2_retry} REJECT={gen_v2_reject}")
    print(f"  错误样本 ACCEPT={imp_v2_accept} RETRY={imp_v2_retry} REJECT={imp_v2_reject} (FAR={stats['v2_far']*100:.1f}%)")

    print(f"\n报告: {report_file}")
    return report


# ============ 工具函数 ============

def run(cmd, timeout=300):
    """运行命令"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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


def show_status():
    """显示数据集状态"""
    report_dir = DATA_DIR / "reports"
    genuine_count = len(list((DATA_DIR / "genuine").glob("*.bin"))) if (DATA_DIR / "genuine").exists() else 0
    impostor_count = len(list((DATA_DIR / "impostor").glob("*.bin"))) if (DATA_DIR / "impostor").exists() else 0
    report_count = len(list(report_dir.glob("*.json"))) if report_dir.exists() else 0

    print(f"数据目录: {DATA_DIR}")
    print(f"模板: {'存在' if TEMPLATE_EXPORT.exists() else '不存在'}")
    print(f"正确样本: {genuine_count}")
    print(f"错误样本: {impostor_count}")
    print(f"匹配报告: {report_count}")


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


def export_template():
    """从系统 fprintd 存储导出模板副本"""
    template = load_template_from_system()
    if not template:
        return False
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATE_EXPORT.write_bytes(template)
    count, width, height, _ = parse_f9rm(template)
    print(f"✓ 模板已导出: {TEMPLATE_EXPORT}")
    print(f"  count={count} size={width}x{height} bytes={len(template)}")
    return True


def template_info():
    """显示当前离线模板信息"""
    template = load_template_for_matching()
    if not template:
        return False
    count, width, height, images = parse_f9rm(template)
    qualities = []
    for i in range(count):
        img = images[i * IMAGE_SIZE:(i + 1) * IMAGE_SIZE]
        qualities.append(analyze_quality(img))

    print(f"模板: {TEMPLATE_EXPORT}")
    print(f"  子模板: {count}")
    print(f"  尺寸: {width}x{height}")
    print(f"  字节: {len(template)}")
    for i, q in enumerate(qualities, 1):
        print(f"  [{i}] mean={q['mean']} stddev={q['stddev']} contrast={q['contrast']} dark={q['dark_pct']}% bright={q['bright_pct']}%")
    return True


def print_help():
    print(__doc__)
    print("\n命令:")
    print("  --enroll-template    录入模板指纹并导出 template.f9rm")
    print("  --export-template    从当前 fprintd 存储导出 template.f9rm")
    print("  --template-info      查看离线模板信息")
    print("  --capture genuine    录制正确手指样本（单次采集，不覆盖模板）")
    print("  --capture impostor   录制错误手指样本（单次采集，不覆盖模板）")
    print("  --capture-once genuine|impostor")
    print("                      非交互采集并保存单张样本（供 GUI 调用）")
    print("  --match              离线匹配计算")
    print("  --status             查看模板、样本和报告数量")
    print("  --list               查看样本列表")
    print("  --reports            查看报告列表")
    print("  --report <file>      显示报告详情")


# ============ CLI 入口 ============

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h", "help"):
        print_help()
        sys.exit(0)

    cmd = sys.argv[1]
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if cmd == "--enroll-template":
        enroll_template()

    elif cmd == "--export-template":
        export_template()

    elif cmd == "--template-info":
        template_info()

    elif cmd == "--capture":
        t = sys.argv[2] if len(sys.argv) > 2 else "genuine"
        if t not in ("genuine", "impostor"):
            print("错误: --capture 只能是 genuine 或 impostor")
            sys.exit(2)
        capture_samples_interactive(t)

    elif cmd == "--capture-once":
        t = sys.argv[2] if len(sys.argv) > 2 else "genuine"
        if t not in ("genuine", "impostor"):
            print("错误: --capture-once 只能是 genuine 或 impostor")
            sys.exit(2)
        if not capture_one_sample(t):
            sys.exit(1)

    elif cmd == "--match":
        run_matching()

    elif cmd == "--status":
        show_status()

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
