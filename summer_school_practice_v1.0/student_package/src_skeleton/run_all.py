from __future__ import annotations
from pathlib import Path
import shutil
import csv
import json

# 路径常量
STUDENT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = STUDENT_PACKAGE_ROOT / "output"

# 导入自己已经完成的各个模块（src_skeleton下同目录）
from m2_protocol import parse_state_vector, encode_position_message, decode_position_message, calculate_checksum
from m3_tracks import main as m3_main
from m4_mapping import main as m4_main
from m5_quality import main as m5_main


def prepare_output_directory() -> None:
    """清空output目录内部文件，保留文件夹本体，规避Windows文件占用PermissionError"""
    import os
    for item in OUTPUT_ROOT.glob("*"):
        try:
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except Exception as e:
            print(f"警告：跳过无法删除的文件 {item} , {e}")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def parse() -> None:
    """
    parse：M2，读取raw_states.json，解析OpenSky原始状态向量，输出结构化解析结果
    """
    print("[M6] Step1: parse OpenSky raw_states.json")
    raw_json_path = STUDENT_PACKAGE_ROOT / "data" / "raw_states.json"
    import json
    with open(raw_json_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
    states_list = raw_data.get("states", [])

    parsed_out = OUTPUT_ROOT / "parsed_open_states.csv"
    rows_out = []
    for vec in states_list:
        prob_list = []
        rec = parse_state_vector(vec, prob_list)
        if rec is not None:   # 过滤解析返回None的坏记录
            rows_out.append(rec)

    if len(rows_out) == 0:
        print("[WARNING] parse没有得到有效记录，跳过输出parsed_open_states.csv")
        return

    fieldnames = list(rows_out[0].keys())
    with open(parsed_out, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fieldnames)
        wr.writeheader()
        wr.writerows(rows_out)
    print(f"[M6] parse完成，输出 {parsed_out}")


def encode() -> None:
    """
    encode：M2编码，读取raw_states.json，生成encoded_messages.bin二进制帧
    """
    print("[M6] Step2: encode TeachingLink 41‑byte messages")
    raw_json_path = STUDENT_PACKAGE_ROOT / "data" / "raw_states.json"
    import json
    with open(raw_json_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
    states_list = raw_data.get("states", [])

    bin_out_path = OUTPUT_ROOT / "encoded_messages.bin"
    seq = 0
    with open(bin_out_path, "wb") as fb:
        for vec in states_list:
            prob_list = []
            record = parse_state_vector(vec, prob_list)
            if record is None:
                continue
            # 新增：编码错误收集列表
            encode_prob = []
            frame_bytes = encode_position_message(record, seq, encode_prob)
            if frame_bytes is not None:
                fb.write(frame_bytes)
            seq = (seq + 1) % 65536
    print(f"[M6] encode完成，二进制帧输出 {bin_out_path}")


def decode_validate() -> None:
    """decode_validate：M2接收端解码校验，读取M2输出的encoded_messages.bin"""
    print("[M6] Step3: decode and validate frames")
    bin_path = OUTPUT_ROOT / "encoded_messages.bin"
    FRAME_SIZE = 41
    with open(bin_path, "rb") as f:
        bin_data = f.read()

    decoded_list = []
    error_log = []
    offset = 0
    total_len = len(bin_data)
    while offset + FRAME_SIZE <= total_len:
        frame_buf = bin_data[offset:offset+FRAME_SIZE]
        dec_res = decode_position_message(frame_buf)
        decoded_list.append(dec_res)
        if dec_res.get("error_list"):
            error_log.extend(dec_res["error_list"])
        offset += FRAME_SIZE

    import csv
    # 输出 decoded_partner_states.csv
    out_csv = OUTPUT_ROOT / "decoded_partner_states.csv"
    header = [
        "message_valid","target_id","timestamp","message_seq","callsign",
        "on_ground","altitude_is_geometric","timestamp_fallback",
        "lat","lon","altitude","speed","heading","vertical_rate"
    ]
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for item in decoded_list:
            w.writerow(item)

    # ========== 【这两段就是生成两个缺失文件的关键】 ==========
    # validation_log.csv
    val_log_path = OUTPUT_ROOT / "validation_log.csv"
    val_header = ["stage","field","problem_type","value","description"]
    with open(val_log_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=val_header)
        w.writeheader()
        w.writerows(error_log)

    # roundtrip_report.csv
    report_path = OUTPUT_ROOT / "roundtrip_report.csv"
    report_header = ["total_frames","valid_frames","invalid_frames","error_count"]
    with open(report_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=report_header)
        w.writeheader()
        w.writerow({
            "total_frames": len(decoded_list),
            "valid_frames": sum(1 for x in decoded_list if x.get("message_valid")),
            "invalid_frames": sum(1 for x in decoded_list if not x.get("message_valid")),
            "error_count": len(error_log)
        })
    # ======================================================

    print(f"[M6] decode_validate完成，输出 {out_csv}，共解码 {len(decoded_list)} 帧")
    print(f"[M6] M2校验日志：{val_log_path}；往返报告：{report_path}")


def build_tracks() -> None:
    """build_tracks：M3批量处理，生成track_table.csv、current_situation.csv"""
    print("[M6] Step4: build track table and current situation")
    m3_main(bin_file_path=OUTPUT_ROOT / "encoded_messages.bin")
    print("[M6] build_tracks完成")


def map_unified() -> None:
    """map_unified：M4，使用人工核验后的verified_mapping_table.csv生成unified_situation.ndjson"""
    print("[M6] Step5: map to unified model (verified mapping table)")
    m4_main()
    print("[M6] map_unified完成")


def check_quality() -> None:
    """check_quality：M5一致性保障，输出alert_log.csv、quality_situation.csv"""
    print("[M6] Step6: consistency quality check")
    m5_main()
    print("[M6] check_quality完成")


def export_results() -> None:
    """export_results：输出运行摘要摘要文本，记录帧数、记录数，写入experiment_summary.txt"""
    print("[M6] Step7: export pipeline summary")
    summary_path = OUTPUT_ROOT / "experiment_summary.txt"

    # 简单统计
    bin_file = OUTPUT_ROOT / "encoded_messages.bin"
    bin_size = bin_file.stat().st_size if bin_file.exists() else 0
    frame_count = bin_size // 41

    ndjson_file = OUTPUT_ROOT / "unified_situation.ndjson"
    ndjson_lines = 0
    if ndjson_file.exists():
        with open(ndjson_file, "r", encoding="utf-8") as f:
            for _ in f:
                ndjson_lines += 1

    alert_csv = OUTPUT_ROOT / "alert_log.csv"
    alert_cnt = 0
    if alert_csv.exists():
        with open(alert_csv, "r", encoding="utf‑8‑sig") as f:
            alert_cnt = sum(1 for _ in f) - 1  # 减去表头

    text = f"""
==== M6 Integrated Pipeline Summary ====
binary total bytes: {bin_size}
total 41‑byte frames: {frame_count}
unified ndjson records: {ndjson_lines}
alert total count: {alert_cnt}

Important notes:
1. Mapping uses verified_mapping_table.csv, NOT raw llm_mapping_candidate.csv
2. Source‑authenticity detection is NOT implemented in M5
3. SQLite is optional, not enabled in this run.
4. All outputs regenerated from empty output directory.
"""
    with open(summary_path, "w", encoding="utf‑8") as f:
        f.write(text)

    print(text)
    print(f"[M6] summary file write to {summary_path}")
    print("==== M6 full pipeline finished ====")


def run_pipeline() -> None:
    run_pipeline.__doc__ = "端到端链路：清空output → parse → encode → decode → track → map → quality → export summary"
    prepare_output_directory()
    parse()
    encode()
    decode_validate()
    build_tracks()
    map_unified()
    check_quality()
    export_results()


def main() -> int:
    try:
        run_pipeline()
    except NotImplementedError as exc:
        print(exc)
        print("当前文件是学生骨架，模块实现完成后再进行端到端运行。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
