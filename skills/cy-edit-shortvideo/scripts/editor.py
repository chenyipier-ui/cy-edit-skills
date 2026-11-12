#!/usr/bin/env python3
"""Local-first helpers for turning a long source video into a Jianying FCPXML rough cut.

cy-edit-shortvideo 剪辑引擎（与长视频共享质量、版式和反馈模块）：在原长视频粗剪工具基础上，于文件末尾追加了扩展：缺 ffmpeg / 转写库 / 模型时当场自动安装（Mac + Windows）、
faster-whisper 转写后端、prepare（整个素材文件夹一条命令）、多素材 validate-multi / compile-multi、
反馈沉淀 feedback。原有命令的校验与编译逻辑未改。

The CLI deliberately keeps the original media in place.  Generated FCPXML files
contain a ``file://`` reference to that media; they never embed or duplicate it.
Only the Python standard library is required.  ``ffmpeg`` and ``ffprobe`` are
used as external executables for media inspection and audio extraction.
"""

from __future__ import annotations

if __name__ == "__main__":
    from pathlib import Path as _CyPath
    from cy_auto_update import start as _cy_start
    _cy_start(_CyPath(__file__).resolve().parents[1])

import argparse
import csv
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
FCPXML_VERSION = "1.8"
DEFAULT_CHUNK_MINUTES = 8.0  # 一块在 CPU 上几分钟内识别完，Agent 单条命令超时后能从断点续上
DEFAULT_PACKET_MINUTES = 8.0
PROJECT_FIELDS = ("name",)
BATCH_PLAN_KIND = "jianying_longvideo_batch_plan"
BATCH_DELIVERY_KIND = "jianying_longvideo_batch_delivery"
BATCH_PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class CliError(RuntimeError):
    """Expected command-line failure with a concise user-facing message."""


@dataclass(frozen=True)
class ValidatedPlan:
    plan: dict[str, Any]
    source_meta: dict[str, Any]
    transcript: dict[str, Any]
    segments: list[dict[str, Any]]
    units_by_id: dict[str, dict[str, Any]]
    source_duration_ms: int
    output_duration_ms: int


@dataclass(frozen=True)
class ValidatedBatchProject:
    project_id: str
    plan_path: Path
    validated: ValidatedPlan


@dataclass(frozen=True)
class ValidatedBatch:
    batch: dict[str, Any]
    batch_path: Path
    source_meta_path: Path
    transcript_path: Path
    source_meta: dict[str, Any]
    transcript: dict[str, Any]
    projects: list[ValidatedBatchProject]


@dataclass(frozen=True)
class TimelineSegment:
    """One edit segment after FCPXML frame-boundary quantization."""

    segment: dict[str, Any]
    source_start: Fraction
    source_end: Fraction
    duration: Fraction
    offset: Fraction


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text atomically within the destination directory."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2, default=_json_default) + "\n",
    )


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.expanduser().open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise CliError(f"{label}不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"{label}不是有效 JSON：{path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise CliError(f"{label}顶层必须是 JSON 对象：{path}")
    return data


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise CliError(f"{label}不存在或不是文件：{resolved}")
    return resolved


def int_ms(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise CliError(f"{label}必须是整数毫秒")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer() and math.isfinite(value):
        return int(value)
    raise CliError(f"{label}必须是整数毫秒")


def seconds_to_ms(value: Any, default: int | None = None) -> int | None:
    if value in (None, "", "N/A"):
        return default
    try:
        decimal = Decimal(str(value)) * 1000
    except (InvalidOperation, ValueError):
        return default
    if not decimal.is_finite():
        return default
    return int(decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_fraction(value: Any) -> Fraction | None:
    if value in (None, "", "0/0", "N/A"):
        return None
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    if result <= 0:
        return None
    return result


def ms_time(milliseconds: int) -> str:
    """Return an exact, reduced FCPXML rational-time expression."""
    return fcpxml_time(Fraction(int(milliseconds), 1000))


def fcpxml_time(fraction: Fraction) -> str:
    """Return an exact, reduced FCPXML rational-time expression."""
    if fraction.denominator == 1:
        return f"{fraction.numerator}s"
    return f"{fraction.numerator}/{fraction.denominator}s"


def round_fraction_to_ms(seconds: Fraction) -> int:
    value = Decimal(seconds.numerator * 1000) / Decimal(seconds.denominator)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def floor_to_frame(seconds: Fraction, frame: Fraction) -> Fraction:
    frame_index = seconds // frame
    return int(frame_index) * frame


def ceil_to_frame(seconds: Fraction, frame: Fraction) -> Fraction:
    ratio = seconds / frame
    frame_index = -(-ratio.numerator // ratio.denominator)
    return frame_index * frame


def parse_fcpxml_time(value: str) -> Fraction:
    if not isinstance(value, str) or not value.endswith("s"):
        raise CliError(f"无效的 FCPXML 时间：{value!r}")
    body = value[:-1]
    try:
        return Fraction(body)
    except (ValueError, ZeroDivisionError) as exc:
        raise CliError(f"无效的 FCPXML 时间：{value!r}") from exc


def fraction_to_ms(value: Fraction, label: str) -> int:
    milliseconds = value * 1000
    if milliseconds.denominator != 1:
        raise CliError(f"{label}不是整毫秒：{value}s")
    return milliseconds.numerator


def format_clock(milliseconds: int, srt: bool = False) -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    separator = "," if srt else "."
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def run_command(command: Sequence[str], label: str, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CliError(f"未找到 {command[0]}；请先安装运行依赖") from exc
    except subprocess.TimeoutExpired as exc:
        raise CliError(f"{label}超时") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        raise CliError(f"{label}失败" + (f"：{detail}" if detail else "")) from exc


def ffprobe_raw(source: Path) -> dict[str, Any]:
    if not shutil.which("ffprobe"):
        from cy_probe import probe
        return probe(source)
    completed = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-print_format",
            "json",
            str(source),
        ],
        "读取媒体信息",
        timeout=300,
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CliError("ffprobe 返回了无效 JSON") from exc
    if not isinstance(result, dict):
        raise CliError("ffprobe 返回结构异常")
    return result


def _stream_duration_ms(stream: dict[str, Any]) -> int | None:
    duration = seconds_to_ms(stream.get("duration"))
    if duration is not None:
        return duration
    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
    tagged = tags.get("DURATION")
    if isinstance(tagged, str):
        match = re.fullmatch(r"(\d+):(\d+):(\d+)(?:[.,](\d+))?", tagged)
        if match:
            hours, minutes, seconds = (int(match.group(i)) for i in range(1, 4))
            fraction = (match.group(4) or "")[:3].ljust(3, "0")
            return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(fraction or 0)
    return None


def normalized_probe(source: Path) -> dict[str, Any]:
    source = require_file(source, "源视频")
    raw = ffprobe_raw(source)
    streams = raw.get("streams") if isinstance(raw.get("streams"), list) else []
    stream_dicts = [stream for stream in streams if isinstance(stream, dict)]
    video_streams = [stream for stream in stream_dicts if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in stream_dicts if stream.get("codec_type") == "audio"]
    format_info = raw.get("format") if isinstance(raw.get("format"), dict) else {}

    duration_ms = seconds_to_ms(format_info.get("duration"))
    if duration_ms is None:
        durations = [duration for stream in stream_dicts if (duration := _stream_duration_ms(stream)) is not None]
        duration_ms = max(durations, default=None)
    if duration_ms is None or duration_ms <= 0:
        raise CliError("无法从媒体中取得有效时长")

    format_start_ms = seconds_to_ms(format_info.get("start_time"), 0) or 0
    usable_video_streams = [
        stream
        for stream in video_streams
        if not (isinstance(stream.get("disposition"), dict) and stream["disposition"].get("attached_pic") == 1)
    ]
    primary_video = max(
        usable_video_streams,
        key=lambda stream: int(stream.get("disposition", {}).get("default", 0))
        if isinstance(stream.get("disposition"), dict)
        else 0,
        default=None,
    )
    primary_audio = max(
        audio_streams,
        key=lambda stream: int(stream.get("disposition", {}).get("default", 0))
        if isinstance(stream.get("disposition"), dict)
        else 0,
        default=None,
    )
    video: dict[str, Any] | None = None
    if primary_video:
        average_fps = parse_fraction(primary_video.get("avg_frame_rate"))
        nominal_fps = parse_fraction(primary_video.get("r_frame_rate"))
        fps = nominal_fps or average_fps
        # 部分安卓可变帧率素材的名义帧率是 90000、120 之类的时基，不是真实帧率：差太多时改用平均帧率并贴到常见帧率
        if average_fps and nominal_fps and (float(nominal_fps) > 1.5 * float(average_fps) or float(nominal_fps) > 120):
            common = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
                      Fraction(50), Fraction(60000, 1001), Fraction(60)]
            near = min(common, key=lambda c: abs(float(c) - float(average_fps)))
            fps = near if abs(float(near) - float(average_fps)) / float(near) < 0.03 else average_fps
        is_vfr = bool(
            average_fps
            and nominal_fps
            and abs(float(average_fps - nominal_fps)) / float(nominal_fps) > 0.001
        )
        stream_start_ms = seconds_to_ms(primary_video.get("start_time"), format_start_ms)
        if stream_start_ms is None:
            stream_start_ms = format_start_ms
        video = {
            "stream_index": primary_video.get("index"),
            "codec": primary_video.get("codec_name"),
            "width": primary_video.get("width"),
            "height": primary_video.get("height"),
            "pixel_format": primary_video.get("pix_fmt"),
            "fps_num": fps.numerator if fps else None,
            "fps_den": fps.denominator if fps else None,
            "fps": float(fps) if fps else None,
            "average_fps_num": average_fps.numerator if average_fps else None,
            "average_fps_den": average_fps.denominator if average_fps else None,
            "nominal_fps_num": nominal_fps.numerator if nominal_fps else None,
            "nominal_fps_den": nominal_fps.denominator if nominal_fps else None,
            "is_vfr": is_vfr,
            "start_ms": stream_start_ms,
            "timeline_start_ms": max(0, stream_start_ms - format_start_ms),
            "duration_ms": _stream_duration_ms(primary_video),
            "frame_count": int(primary_video["nb_frames"]) if str(primary_video.get("nb_frames", "")).isdigit() else None,
            "color_primaries": primary_video.get("color_primaries"),
            "color_transfer": primary_video.get("color_transfer"),
            "color_space": primary_video.get("color_space"),
        }
    audio: dict[str, Any] | None = None
    if primary_audio:
        channels = primary_audio.get("channels")
        sample_rate = primary_audio.get("sample_rate")
        stream_start_ms = seconds_to_ms(primary_audio.get("start_time"), format_start_ms)
        if stream_start_ms is None:
            stream_start_ms = format_start_ms
        audio = {
            "stream_index": primary_audio.get("index"),
            "codec": primary_audio.get("codec_name"),
            "sample_rate": int(sample_rate) if str(sample_rate or "").isdigit() else None,
            "channels": int(channels) if isinstance(channels, (int, str)) and str(channels).isdigit() else None,
            "channel_layout": primary_audio.get("channel_layout"),
            "start_ms": stream_start_ms,
            "timeline_start_ms": max(0, stream_start_ms - format_start_ms),
            "duration_ms": _stream_duration_ms(primary_audio),
        }

    stat = source.stat()
    return {
        "schema_version": SCHEMA_VERSION,
        "source_path": str(source),
        "source_uri": source.as_uri(),
        "filename": source.name,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "duration_ms": duration_ms,
        "start_ms": format_start_ms,
        "format_name": format_info.get("format_name"),
        "bit_rate": int(format_info["bit_rate"]) if str(format_info.get("bit_rate", "")).isdigit() else None,
        "video_stream_count": len(video_streams),
        "audio_stream_count": len(audio_streams),
        "audio_streams": [{"index":x["index"],"channels":x.get("channels",2)} for x in audio_streams],
        "video": video,
        "audio": audio,
    }


def ensure_schema_version(data: dict[str, Any], label: str) -> None:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CliError(f"{label}.schema_version 必须为 {SCHEMA_VERSION}")


def same_path(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()


def validate_source_fingerprint(source_meta: dict[str, Any]) -> Path:
    """Refuse to compile against a same-name source that changed after probe."""
    source_value = source_meta.get("source_path")
    if not isinstance(source_value, str) or not source_value:
        raise CliError("source_meta.source_path 缺失")
    source = require_file(Path(source_value), "source_meta 中的源视频")
    stat = source.stat()
    expected_size = source_meta.get("size_bytes")
    expected_mtime = source_meta.get("mtime_ns")
    if not isinstance(expected_size, int) or not isinstance(expected_mtime, int):
        raise CliError("source_meta 缺少 size_bytes/mtime_ns；请重新运行 probe")
    if stat.st_size != expected_size or stat.st_mtime_ns != expected_mtime:
        raise CliError("源视频在 probe 后发生变化；为避免错片，请重新运行 probe、转写和计划校验")
    return source


def transcript_units(transcript: dict[str, Any], source_duration_ms: int) -> list[dict[str, Any]]:
    ensure_schema_version(transcript, "transcript")
    raw_segments = transcript.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise CliError("transcript.segments 必须是非空数组")
    units: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_start = -1
    for index, raw in enumerate(raw_segments, 1):
        if not isinstance(raw, dict):
            raise CliError(f"transcript.segments[{index}] 必须是对象")
        unit_id = raw.get("id")
        if not isinstance(unit_id, str) or not unit_id.strip():
            raise CliError(f"transcript.segments[{index}].id 不能为空")
        if unit_id in seen:
            raise CliError(f"转写单元 id 重复：{unit_id}")
        seen.add(unit_id)
        start = int_ms(raw.get("start_ms"), f"{unit_id}.start_ms")
        end = int_ms(raw.get("end_ms"), f"{unit_id}.end_ms")
        if not 0 <= start < end <= source_duration_ms:
            raise CliError(f"转写单元 {unit_id} 越界：{start}-{end}ms")
        if start < previous_start:
            raise CliError("transcript.segments 必须按 start_ms 升序排列")
        previous_start = start
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise CliError(f"转写单元 {unit_id}.text 不能为空")
        words = raw.get("words", [])
        if not isinstance(words, list):
            raise CliError(f"转写单元 {unit_id}.words 必须是数组")
        normalized_words: list[dict[str, Any]] = []
        for word_index, word in enumerate(words, 1):
            if not isinstance(word, dict):
                raise CliError(f"{unit_id}.words[{word_index}] 必须是对象")
            word_start = int_ms(word.get("start_ms"), f"{unit_id}.words[{word_index}].start_ms")
            word_end = int_ms(word.get("end_ms"), f"{unit_id}.words[{word_index}].end_ms")
            if not start <= word_start < word_end <= end:
                raise CliError(f"{unit_id}.words[{word_index}] 时间越界")
            if not isinstance(word.get("text"), str) or not word["text"]:
                raise CliError(f"{unit_id}.words[{word_index}].text 不能为空")
            normalized_word = dict(word)
            normalized_word.update({"start_ms": word_start, "end_ms": word_end, "text": word["text"]})
            normalized_words.append(normalized_word)
        normalized_words.sort(key=lambda item: (item["start_ms"], item["end_ms"]))
        unit = dict(raw)
        unit["id"] = unit_id
        unit["start_ms"] = start
        unit["end_ms"] = end
        unit["text"] = text.strip()
        unit["words"] = normalized_words
        units.append(unit)
    return units


def evidence_text(units: Iterable[dict[str, Any]]) -> str:
    return " ".join(str(unit.get("text", "")).strip() for unit in units).strip()


def interval_evidence_text(units: Iterable[dict[str, Any]], start: int, end: int) -> str:
    """Return only evidence that is actually audible inside a kept interval."""
    parts: list[str] = []
    for unit in units:
        words = unit.get("words")
        if isinstance(words, list) and words:
            selected = [
                str(word.get("text", ""))
                for word in words
                if isinstance(word, dict)
                and int(word.get("end_ms", -1)) > start
                and int(word.get("start_ms", end)) < end
            ]
            if selected:
                parts.append("".join(selected).strip())
            continue
        # SRT has no word timestamps. At least 80% of that subtitle unit must
        # remain; a tiny overlap must never prove words outside the cut.
        unit_start = int(unit["start_ms"])
        unit_end = int(unit["end_ms"])
        overlap = max(0, min(end, unit_end) - max(start, unit_start))
        if overlap * 5 >= (unit_end - unit_start) * 4:
            parts.append(str(unit.get("text", "")).strip())
    return " ".join(part for part in parts if part).strip()


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return "".join(character.casefold() for character in value if character.isalnum())


def validate_plan_data(
    plan: dict[str, Any], source_meta: dict[str, Any], transcript: dict[str, Any]
) -> ValidatedPlan:
    ensure_schema_version(plan, "plan")
    content_mode = plan.get("content_mode")
    if content_mode is not None and content_mode not in {"single_longform", "multi_clip"}:
        raise CliError("plan.content_mode 只能是 single_longform 或 multi_clip")
    ensure_schema_version(source_meta, "source_meta")
    ensure_schema_version(transcript, "transcript")

    source_path = source_meta.get("source_path")
    if not isinstance(source_path, str) or not source_path:
        raise CliError("source_meta.source_path 缺失")
    validate_source_fingerprint(source_meta)
    source_duration_ms = int_ms(source_meta.get("duration_ms"), "source_meta.duration_ms")
    if source_duration_ms <= 0:
        raise CliError("source_meta.duration_ms 必须大于 0")
    if not same_path(transcript.get("source_path"), source_path):
        raise CliError("transcript.source_path 与 source_meta.source_path 不一致")
    transcript_duration = transcript.get("duration_ms")
    if transcript_duration is not None and int_ms(transcript_duration, "transcript.duration_ms") != source_duration_ms:
        raise CliError("transcript.duration_ms 与 source_meta.duration_ms 不一致")

    project = plan.get("project")
    if not isinstance(project, dict):
        raise CliError("plan.project 必须是对象")
    for field in PROJECT_FIELDS:
        value = project.get(field)
        if not isinstance(value, str) or not value.strip():
            raise CliError(f"plan.project.{field} 不能为空")

    plan_source = plan.get("source")
    if not isinstance(plan_source, dict):
        raise CliError("plan.source 必须是对象")
    if not same_path(plan_source.get("path"), source_path):
        raise CliError("plan.source.path 与 source_meta.source_path 不一致")
    if int_ms(plan_source.get("duration_ms"), "plan.source.duration_ms") != source_duration_ms:
        raise CliError("plan.source.duration_ms 与 source_meta.duration_ms 不一致")

    units = transcript_units(transcript, source_duration_ms)
    units_by_id = {unit["id"]: unit for unit in units}
    raw_segments = plan.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise CliError("plan.segments 必须是非空数组")

    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    segments: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_segments, 1):
        if not isinstance(raw, dict):
            raise CliError(f"plan.segments[{index}] 必须是对象")
        segment_id = raw.get("id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise CliError(f"plan.segments[{index}].id 不能为空")
        if segment_id in seen_ids:
            raise CliError(f"剪辑段 id 重复：{segment_id}")
        seen_ids.add(segment_id)
        order = int_ms(raw.get("order"), f"{segment_id}.order")
        if order <= 0 or order in seen_orders:
            raise CliError(f"剪辑段 {segment_id}.order 必须是唯一正整数")
        seen_orders.add(order)
        start = int_ms(raw.get("source_start_ms"), f"{segment_id}.source_start_ms")
        end = int_ms(raw.get("source_end_ms"), f"{segment_id}.source_end_ms")
        if not 0 <= start < end <= source_duration_ms:
            raise CliError(f"剪辑段 {segment_id} 越界：{start}-{end}ms")
        role = raw.get("role")
        reason = raw.get("reason")
        if not isinstance(role, str) or not role.strip():
            raise CliError(f"剪辑段 {segment_id}.role 不能为空")
        if not isinstance(reason, str) or not reason.strip():
            raise CliError(f"剪辑段 {segment_id}.reason 不能为空")
        evidence_ids = raw.get("evidence_unit_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise CliError(f"剪辑段 {segment_id}.evidence_unit_ids 必须是非空数组")
        if not all(isinstance(evidence_id, str) and evidence_id for evidence_id in evidence_ids):
            raise CliError(f"剪辑段 {segment_id}.evidence_unit_ids 只能包含非空字符串")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise CliError(f"剪辑段 {segment_id} 的 evidence_unit_ids 有重复")
        referenced_units: list[dict[str, Any]] = []
        for evidence_id in evidence_ids:
            if not isinstance(evidence_id, str) or evidence_id not in units_by_id:
                raise CliError(f"剪辑段 {segment_id} 引用了不存在的证据单元：{evidence_id!r}")
            unit = units_by_id[evidence_id]
            if unit["end_ms"] <= start or unit["start_ms"] >= end:
                raise CliError(f"剪辑段 {segment_id} 的证据单元 {evidence_id} 与保留区间不相交")
            referenced_units.append(unit)
        coverage_intervals = sorted(
            (max(start, int(unit["start_ms"])), min(end, int(unit["end_ms"])))
            for unit in referenced_units
        )
        merged_coverage: list[list[int]] = []
        for coverage_start, coverage_end in coverage_intervals:
            if coverage_end <= coverage_start:
                continue
            if merged_coverage and coverage_start <= merged_coverage[-1][1]:
                merged_coverage[-1][1] = max(merged_coverage[-1][1], coverage_end)
            else:
                merged_coverage.append([coverage_start, coverage_end])
        covered_ms = sum(coverage_end - coverage_start for coverage_start, coverage_end in merged_coverage)
        gaps: list[int] = []
        cursor = start
        for coverage_start, coverage_end in merged_coverage:
            gaps.append(max(0, coverage_start - cursor))
            cursor = max(cursor, coverage_end)
        gaps.append(max(0, end - cursor))
        max_gap_ms = max(gaps, default=end - start)
        needs_confirmation = raw.get("needs_confirmation") is True
        if not needs_confirmation and (
            covered_ms * 2 < end - start or max_gap_ms > max(5000, (end - start) // 4)
        ):
            raise CliError(
                f"剪辑段 {segment_id} 的证据覆盖不足（覆盖 {covered_ms}ms，最大空洞 {max_gap_ms}ms）；"
                "请补齐证据单元、缩短区间，或设置 needs_confirmation=true 交给人工复核"
            )
        plan_transcript = raw.get("transcript")
        if not isinstance(plan_transcript, str) or not plan_transcript.strip():
            raise CliError(f"剪辑段 {segment_id}.transcript 必须是源转写原文")
        normalized_plan_text = normalized_text(plan_transcript)
        normalized_evidence = normalized_text(interval_evidence_text(referenced_units, start, end))
        if not normalized_plan_text or normalized_plan_text not in normalized_evidence:
            raise CliError(
                f"剪辑段 {segment_id}.transcript 无法在保留区间内的证据中找到；"
                "请调整切点、补 word timestamps 或标出真实原文"
            )

        segment = dict(raw)
        segment.update(
            {
                "id": segment_id,
                "order": order,
                "source_start_ms": start,
                "source_end_ms": end,
                "role": role.strip(),
                "reason": reason.strip(),
                "transcript": plan_transcript.strip(),
                "evidence_unit_ids": evidence_ids,
            }
        )
        segments.append(segment)

    expected_orders = list(range(1, len(segments) + 1))
    if sorted(seen_orders) != expected_orders:
        raise CliError(f"plan.segments.order 必须连续为 1..{len(segments)}")
    segments.sort(key=lambda item: item["order"])

    allow_all_overlap = plan.get("allow_source_overlap") is True
    for left_index, left in enumerate(segments):
        for right in segments[left_index + 1 :]:
            overlaps = max(left["source_start_ms"], right["source_start_ms"]) < min(
                left["source_end_ms"], right["source_end_ms"]
            )
            if overlaps and not (
                allow_all_overlap
                or (left.get("allow_source_overlap") is True and right.get("allow_source_overlap") is True)
            ):
                raise CliError(
                    f"剪辑段 {left['id']} 与 {right['id']} 的源区间重叠；"
                    "若确属有意复用，须在两个段上都设置 allow_source_overlap=true"
                )

    frame = frame_duration_fraction(source_meta)
    quantized_ranges = {
        segment["id"]: (
            floor_to_frame(Fraction(segment["source_start_ms"], 1000), frame),
            ceil_to_frame(Fraction(segment["source_end_ms"], 1000), frame),
        )
        for segment in segments
    }
    for left_index, left in enumerate(segments):
        for right in segments[left_index + 1 :]:
            left_start, left_end = quantized_ranges[left["id"]]
            right_start, right_end = quantized_ranges[right["id"]]
            overlaps_after_quantization = max(left_start, right_start) < min(left_end, right_end)
            overlap_allowed = allow_all_overlap or (
                left.get("allow_source_overlap") is True and right.get("allow_source_overlap") is True
            )
            if overlaps_after_quantization and not overlap_allowed:
                raise CliError(
                    f"剪辑段 {left['id']} 与 {right['id']} 在帧边界量化后重叠；"
                    "请合并相邻段或把切点分开至少一帧"
                )

    output_duration_ms = sum(segment["source_end_ms"] - segment["source_start_ms"] for segment in segments)
    return ValidatedPlan(
        plan=plan,
        source_meta=source_meta,
        transcript=transcript,
        segments=segments,
        units_by_id=units_by_id,
        source_duration_ms=source_duration_ms,
        output_duration_ms=output_duration_ms,
    )


def validate_inputs(plan_path: Path, meta_path: Path, transcript_path: Path) -> ValidatedPlan:
    return validate_plan_data(
        load_json(plan_path, "剪辑计划"),
        load_json(meta_path, "媒体元数据"),
        load_json(transcript_path, "转写文件"),
    )


def resolve_batch_reference(batch_path: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise CliError(f"{label} 必须是非空路径")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = batch_path.parent / path
    return require_file(path, label)


def validate_batch_inputs(batch_path_value: Path) -> ValidatedBatch:
    batch_path = require_file(batch_path_value, "批量计划")
    batch = load_json(batch_path, "批量计划")
    ensure_schema_version(batch, "batch")
    if batch.get("kind") != BATCH_PLAN_KIND:
        raise CliError(f"batch.kind 必须为 {BATCH_PLAN_KIND!r}")
    batch_id = batch.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id.strip() or len(batch_id) > 128:
        raise CliError("batch.batch_id 必须是 1–128 字符的非空字符串")

    source_meta_path = resolve_batch_reference(batch_path, batch.get("source_meta"), "batch.source_meta")
    transcript_path = resolve_batch_reference(batch_path, batch.get("transcript"), "batch.transcript")
    source_meta = load_json(source_meta_path, "媒体元数据")
    transcript = load_json(transcript_path, "转写文件")

    raw_projects = batch.get("projects")
    if not isinstance(raw_projects, list) or not 1 <= len(raw_projects) <= 3:
        raise CliError("batch.projects 必须包含 1–3 个独立成片计划；不值得剪时不要创建批量计划")
    recommended_count = batch.get("recommended_count")
    if recommended_count is not None:
        if isinstance(recommended_count, bool) or not isinstance(recommended_count, int):
            raise CliError("batch.recommended_count 必须是整数")
        if recommended_count != len(raw_projects):
            raise CliError("batch.recommended_count 必须与 projects 数量一致")

    seen_ids: set[str] = set()
    seen_plan_paths: set[Path] = set()
    seen_project_names: set[str] = set()
    projects: list[ValidatedBatchProject] = []
    for index, raw_project in enumerate(raw_projects, 1):
        if not isinstance(raw_project, dict):
            raise CliError(f"batch.projects[{index}] 必须是对象")
        project_id = raw_project.get("id")
        if not isinstance(project_id, str) or not BATCH_PROJECT_ID_PATTERN.fullmatch(project_id):
            raise CliError(
                f"batch.projects[{index}].id 必须匹配 [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}"
            )
        normalized_id = project_id.casefold()
        if normalized_id in seen_ids:
            raise CliError(f"批量项目 id 重复（忽略大小写）：{project_id}")
        seen_ids.add(normalized_id)
        plan_path = resolve_batch_reference(
            batch_path, raw_project.get("plan"), f"batch.projects[{index}].plan"
        )
        if plan_path in seen_plan_paths:
            raise CliError(f"同一 edit_plan 被重复引用：{plan_path}")
        seen_plan_paths.add(plan_path)
        try:
            validated = validate_plan_data(
                load_json(plan_path, f"批量项目 {project_id} 的剪辑计划"),
                source_meta,
                transcript,
            )
        except CliError as exc:
            raise CliError(f"批量项目 {project_id} 校验失败：{exc}") from exc
        if validated.plan.get("content_mode") not in (None, "multi_clip"):
            raise CliError(
                f"批量项目 {project_id} 的 content_mode 必须为 multi_clip"
            )
        project_name_key = unicodedata.normalize(
            "NFKC", sanitize_filename(validated.plan["project"]["name"])
        ).casefold()
        if project_name_key in seen_project_names:
            raise CliError(
                f"批量项目名清理后重复：{validated.plan['project']['name']!r}；请给每个剪映工程唯一名称"
            )
        seen_project_names.add(project_name_key)
        projects.append(
            ValidatedBatchProject(
                project_id=project_id,
                plan_path=plan_path,
                validated=validated,
            )
        )

    return ValidatedBatch(
        batch=batch,
        batch_path=batch_path,
        source_meta_path=source_meta_path,
        transcript_path=transcript_path,
        source_meta=source_meta,
        transcript=transcript,
        projects=projects,
    )


def command_doctor(args: argparse.Namespace) -> int:
    executables: dict[str, Any] = {}
    for executable in ("ffmpeg", "ffprobe"):
        location = shutil.which(executable)
        item: dict[str, Any] = {"found": bool(location), "path": location}
        if location:
            try:
                version = run_command([executable, "-version"], f"读取 {executable} 版本", timeout=30)
                item["version"] = (version.stdout.splitlines() or [""])[0]
            except CliError as exc:
                item["version_error"] = str(exc)
        executables[executable] = item
    try:
        import importlib.util

        mlx_whisper_found = importlib.util.find_spec("mlx_whisper") is not None
        faster_whisper_found = importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        mlx_whisper_found = faster_whisper_found = False
    _locate_ffmpeg()

    disk_path = Path(args.workdir).expanduser().resolve() if args.workdir else Path.cwd()
    disk_path.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(disk_path)
    ready = executables["ffmpeg"]["found"] and (executables["ffprobe"]["found"] or importlib.util.find_spec("av") is not None)
    recommended_free_bytes = 12 * 1024**3
    notes = [
        "转写库和模型不用预装：第一次转写时自动安装、自动下载（Apple 芯片 Mac 用 mlx-whisper，其他用 faster-whisper）。",
        "缺媒体工具时，媒体命令自动安装用户级 FFmpeg 与 PyAV，不依赖 Homebrew 或 winget。",
        "长视频工作目录应放在有足够空间的源盘或外置盘。",
    ]
    if disk.free < recommended_free_bytes:
        notes.append(
            "当前工作盘可用空间低于建议的 12 GiB；请把 --workdir 和 --model-cache 改到空间充足的外置盘。"
        )
    report = {
        "ready": ready,
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "system": {"platform": platform.platform(), "machine": platform.machine()},
        "executables": executables,
        "optional_python_modules": {"mlx_whisper": mlx_whisper_found, "faster_whisper": faster_whisper_found},
        "transcribe_backend": pick_backend("auto"),
        "workdir": str(disk_path),
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "recommended_free_bytes": recommended_free_bytes,
            "long_video_headroom_ok": disk.free >= recommended_free_bytes,
        },
        "notes": notes,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ready else 1


def command_probe(args: argparse.Namespace) -> int:
    metadata = normalized_probe(Path(args.source))
    atomic_write_json(Path(args.out), metadata)
    print(json.dumps({"ok": True, "out": str(Path(args.out).expanduser().resolve()), "metadata": metadata}, ensure_ascii=False, indent=2))
    return 0


def probe_audio_duration(path: Path) -> int | None:
    if not shutil.which("ffprobe"):
        from cy_probe import audio_duration
        return audio_duration(path)
    # FLAC files written by ffmpeg's segment muxer cannot have their STREAMINFO
    # duration patched until the final segment, and reset_timestamps is not
    # honored consistently.  The difference between the first packet PTS and
    # maximum packet end is the reliable decodable duration.
    packet_probe = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "packet=pts_time,duration_time",
            "-of",
            "csv=p=0",
            str(path),
        ],
        f"扫描音频分片时间戳 {path.name}",
        timeout=300,
    )
    minimum_start: Decimal | None = None
    maximum_end: Decimal | None = None
    for line in packet_probe.stdout.splitlines():
        values = [value.strip() for value in line.split(",")]
        if len(values) < 2:
            continue
        try:
            pts = Decimal(values[0])
            packet_duration = Decimal(values[1])
        except InvalidOperation:
            continue
        minimum_start = pts if minimum_start is None else min(minimum_start, pts)
        packet_end = pts + packet_duration
        maximum_end = packet_end if maximum_end is None else max(maximum_end, packet_end)
    if minimum_start is None or maximum_end is None:
        return None
    duration = seconds_to_ms(maximum_end - minimum_start)
    return duration if duration is not None and duration > 0 else None


def _silence_split_times(source: Path, stream_index: Any, audio_filter: str, chunk_seconds: float, total_seconds: float) -> list[float]:
    """分块切点挪到目标时间附近最近的停顿中点，避免把一个词切成两半（转写分块之间没有重叠）。
    检测失败或附近没有停顿时用固定时间。"""
    if total_seconds <= chunk_seconds * 1.25:
        return []
    try:
        proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(source), "-map", f"0:{stream_index}",
                               "-af", audio_filter + ",silencedetect=noise=-35dB:d=0.35", "-vn", "-f", "null", "-"],
                              capture_output=True, text=True, errors="replace", timeout=1800)
        starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
        ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", proc.stderr)]
    except Exception:  # noqa: BLE001
        starts, ends = [], []
    mids = [(x + y) / 2 for x, y in zip(starts, ends) if y > x]
    window = min(60.0, chunk_seconds / 4)
    times: list[float] = []
    target = chunk_seconds
    while target < total_seconds - chunk_seconds / 4:
        near = [m for m in mids if abs(m - target) <= window]
        point = min(near, key=lambda m: abs(m - target)) if near else target
        times.append(round(point, 3))
        target = point + chunk_seconds
    return times


def command_extract_audio(args: argparse.Namespace) -> int:
    source = require_file(Path(args.source), "源视频")
    metadata = normalized_probe(source)
    audio_metadata = metadata.get("audio")
    if not isinstance(audio_metadata, dict):
        raise CliError("源视频没有可用音轨")
    chunk_minutes = float(args.chunk_minutes)
    if not math.isfinite(chunk_minutes) or chunk_minutes <= 0:
        raise CliError("--chunk-minutes 必须大于 0")
    workdir = Path(args.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    final_dir = workdir / "audio_chunks"
    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else workdir / "audio_manifest.json"
    if final_dir.exists():
        raise CliError(f"音频分片目录已存在，为避免覆盖已停止：{final_dir}")

    temp_dir = Path(tempfile.mkdtemp(prefix=".audio_chunks.", dir=workdir))
    try:
        output_pattern = temp_dir / "audio_%04d.flac"
        audio_filter = f"asetpts=PTS-STARTPTS+({(audio_metadata['start_ms']-metadata['start_ms'])/1000})/TB,aresample=16000:async=1:first_pts=0"
        split_times = _silence_split_times(source, audio_metadata['stream_index'], audio_filter, chunk_minutes * 60, (metadata.get('duration_ms') or 0) / 1000)
        run_command(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-map",
                f"0:{audio_metadata['stream_index']}",
                "-af", f"asetpts=PTS-STARTPTS+({(audio_metadata['start_ms']-metadata['start_ms'])/1000})/TB,aresample=16000:async=1:first_pts=0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "flac",
                "-compression_level",
                "5",
                "-f",
                "segment",
                *(["-segment_times", ",".join(f"{t:.3f}" for t in split_times)] if split_times
                  else ["-segment_time", f"{chunk_minutes * 60:.3f}"]),
                "-reset_timestamps",
                "1",
                str(output_pattern),
            ],
            "抽取并切分音频",
            timeout=None,
        )
        temp_chunks = sorted(temp_dir.glob("audio_*.flac"))
        if not temp_chunks:
            raise CliError("ffmpeg 未生成任何音频分片")
        usable_chunks: list[tuple[Path, int]] = []
        for path in temp_chunks:
            duration = probe_audio_duration(path)
            if duration is None:
                # The segment muxer can emit an empty trailing FLAC exactly on
                # a boundary.  It contains no audio packets and is not a chunk.
                path.unlink()
                continue
            usable_chunks.append((path, duration))
        if not usable_chunks:
            raise CliError("音频分片均为空")
        os.replace(temp_dir, final_dir)
        temp_dir = final_dir  # Prevent cleanup after the atomic directory move.

        chunks: list[dict[str, Any]] = []
        audio_timeline_start_ms = int(audio_metadata.get("timeline_start_ms") or 0)
        cursor = audio_timeline_start_ms
        durations_by_name = {path.name: duration for path, duration in usable_chunks}
        for index, chunk_path in enumerate(sorted(final_dir.glob("audio_*.flac")), 1):
            duration = durations_by_name[chunk_path.name]
            chunks.append(
                {
                    "id": f"c{index:04d}",
                    "path": str(chunk_path.resolve()),
                    "start_ms": cursor,
                    "end_ms": cursor + duration,
                    "duration_ms": duration,
                }
            )
            cursor += duration
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "source_path": str(source),
            "source_duration_ms": metadata["duration_ms"],
            "source_size_bytes": metadata["size_bytes"],
            "source_mtime_ns": metadata["mtime_ns"],
            "audio_timeline_start_ms": audio_timeline_start_ms,
            "expected_audio_end_ms": min(
                metadata["duration_ms"],
                audio_timeline_start_ms + int(audio_metadata.get("duration_ms") or (cursor - audio_timeline_start_ms)),
            ),
            "sample_rate": 16000,
            "channels": 1,
            "codec": "flac",
            "chunk_minutes": chunk_minutes,
            "chunks": chunks,
        }
        atomic_write_json(manifest_path, manifest)
    except BaseException:
        if temp_dir.exists() and temp_dir.name.startswith(".audio_chunks."):
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(manifest_path),
                "chunks_dir": str(final_dir),
                "chunk_count": len(chunks),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def validate_audio_manifest(manifest: dict[str, Any]) -> tuple[Path, int, list[dict[str, Any]]]:
    ensure_schema_version(manifest, "manifest")
    source_path = manifest.get("source_path")
    if not isinstance(source_path, str) or not source_path:
        raise CliError("manifest.source_path 缺失")
    source = require_file(Path(source_path), "manifest 中的源视频")
    stat = source.stat()
    if manifest.get("source_size_bytes") != stat.st_size or manifest.get("source_mtime_ns") != stat.st_mtime_ns:
        raise CliError("源视频在抽取音频后发生变化；请删除旧音频工作目录并重新开始")
    source_duration = int_ms(manifest.get("source_duration_ms"), "manifest.source_duration_ms")
    if source_duration <= 0:
        raise CliError("manifest.source_duration_ms 必须大于 0")
    raw_chunks = manifest.get("chunks")
    if not isinstance(raw_chunks, list) or not raw_chunks:
        raise CliError("manifest.chunks 必须是非空数组")
    chunks: list[dict[str, Any]] = []
    seen: set[str] = set()
    audio_start = int_ms(manifest.get("audio_timeline_start_ms", 0), "manifest.audio_timeline_start_ms")
    if not 0 <= audio_start <= source_duration:
        raise CliError("manifest.audio_timeline_start_ms 越界")
    previous_end = audio_start
    for index, raw in enumerate(raw_chunks, 1):
        if not isinstance(raw, dict):
            raise CliError(f"manifest.chunks[{index}] 必须是对象")
        chunk_id = raw.get("id")
        if not isinstance(chunk_id, str) or not chunk_id or chunk_id in seen:
            raise CliError(f"manifest.chunks[{index}].id 必须是唯一非空字符串")
        seen.add(chunk_id)
        chunk_path_value = raw.get("path")
        if not isinstance(chunk_path_value, str):
            raise CliError(f"音频分片 {chunk_id}.path 缺失")
        chunk_path = require_file(Path(chunk_path_value), f"音频分片 {chunk_id}")
        start = int_ms(raw.get("start_ms"), f"{chunk_id}.start_ms")
        end = int_ms(raw.get("end_ms"), f"{chunk_id}.end_ms")
        duration = int_ms(raw.get("duration_ms"), f"{chunk_id}.duration_ms")
        if start != previous_end:
            raise CliError(f"音频分片 {chunk_id} 与上一分片不连续：应从 {previous_end}ms 开始")
        if start < 0 or end <= start or duration != end - start:
            raise CliError(f"音频分片 {chunk_id} 的时间字段无效")
        chunk = dict(raw)
        chunk.update({"id": chunk_id, "path": str(chunk_path), "start_ms": start, "end_ms": end, "duration_ms": duration})
        chunks.append(chunk)
        previous_end = end
    expected_end = int_ms(manifest.get("expected_audio_end_ms"), "manifest.expected_audio_end_ms")
    if not 0 <= expected_end <= source_duration:
        raise CliError("manifest.expected_audio_end_ms 越界")
    if abs(previous_end - expected_end) > 250:
        raise CliError(
            f"音频分片末尾 {previous_end}ms 与预期 {expected_end}ms 相差超过 250ms；"
            "可能存在截断或漏块"
        )
    # AAC padding can make extracted audio end a few milliseconds after the
    # container duration.  It is clipped to source_duration during assembly.
    return source, source_duration, chunks


def checkpoint_signature(chunk: dict[str, Any], model: str, language: str) -> dict[str, Any]:
    path = Path(chunk["path"])
    stat = path.stat()
    return {
        "chunk_id": chunk["id"],
        "path": str(path.resolve()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "start_ms": chunk["start_ms"],
        "end_ms": chunk["end_ms"],
        "model": model,
        "language": language,
    }


def read_reusable_checkpoint(
    path: Path, signature: dict[str, Any]
) -> tuple[list[dict[str, Any]], str | None] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return None
    if data.get("signature") != signature or not isinstance(data.get("segments"), list):
        return None
    detected = data.get("detected_language")
    return data["segments"], detected if isinstance(detected, str) and detected else None


def normalize_mlx_segments(result: dict[str, Any], chunk_duration_ms: int) -> list[dict[str, Any]]:
    raw_segments = result.get("segments")
    if not isinstance(raw_segments, list):
        raise CliError("mlx_whisper 返回结构中缺少 segments 数组")
    segments: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_segments, 1):
        if not isinstance(raw, dict):
            continue
        start = seconds_to_ms(raw.get("start"))
        end = seconds_to_ms(raw.get("end"))
        text_value = raw.get("text")
        if start is None or end is None or not isinstance(text_value, str) or not text_value.strip():
            continue
        start = max(0, min(start, chunk_duration_ms))
        end = max(0, min(end, chunk_duration_ms))
        if end <= start:
            continue
        words: list[dict[str, Any]] = []
        raw_words = raw.get("words")
        if isinstance(raw_words, list):
            for word in raw_words:
                if not isinstance(word, dict):
                    continue
                word_start = seconds_to_ms(word.get("start"))
                word_end = seconds_to_ms(word.get("end"))
                word_text = word.get("word", word.get("text"))
                if word_start is None or word_end is None or not isinstance(word_text, str) or not word_text:
                    continue
                word_start = max(start, min(word_start, end))
                word_end = max(start, min(word_end, end))
                if word_end <= word_start:
                    continue
                words.append({"start_ms": word_start, "end_ms": word_end, "text": word_text})
        segments.append(
            {
                "start_ms": start,
                "end_ms": end,
                "text": text_value.strip(),
                "words": words,
                "mlx_segment_index": index,
            }
        )
    segments.sort(key=lambda item: (item["start_ms"], item["end_ms"]))
    return segments


def command_transcribe(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_json(manifest_path, "音频 manifest")
    source, source_duration, chunks = validate_audio_manifest(manifest)
    model = str(args.model).strip()
    language = str(args.language).strip()
    if not model:
        raise CliError("--model 不能为空")
    if not language:
        raise CliError("--language 不能为空；自动识别请传 auto")

    model_cache = Path(args.model_cache).expanduser().resolve()
    model_cache.mkdir(parents=True, exist_ok=True)
    # Set both variables before the lazy import so model weights never default
    # to the nearly-full system disk.
    os.environ["HF_HOME"] = str(model_cache)
    os.environ["HF_HUB_CACHE"] = str(model_cache / "hub")

    output = Path(args.out).expanduser().resolve()
    job_material = json.dumps(
        {"source": str(source), "model": model, "language": language}, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    job_id = hashlib.sha256(job_material).hexdigest()[:16]
    checkpoint_dir = output.parent / ".transcribe_checkpoints" / job_id
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    mlx_module: Any | None = None
    global_segments: list[dict[str, Any]] = []
    reused = 0
    transcribed = 0
    detected_languages: list[str] = []
    for index, chunk in enumerate(chunks, 1):
        signature = checkpoint_signature(chunk, model, language)
        checkpoint_path = checkpoint_dir / f"{chunk['id']}.json"
        checkpoint = read_reusable_checkpoint(checkpoint_path, signature)
        if checkpoint is not None:
            local_segments, checkpoint_language = checkpoint
            if checkpoint_language:
                detected_languages.append(checkpoint_language)
            reused += 1
            print(f"[{index}/{len(chunks)}] 复用转写 checkpoint：{chunk['id']}", file=sys.stderr)
        else:
            if mlx_module is None:
                try:
                    import mlx_whisper as mlx_module  # type: ignore[no-redef,import-not-found]
                except ImportError as exc:
                    raise CliError(
                        "未安装 mlx_whisper；transcribe 命令会自动安装，请直接运行 transcribe"
                    ) from exc
            print(f"[{index}/{len(chunks)}] 转写音频分片：{chunk['id']}", file=sys.stderr)
            try:
                result = mlx_module.transcribe(
                    chunk["path"],
                    path_or_hf_repo=model,
                    language=None if language.lower() == "auto" else language,
                    word_timestamps=True,
                )
            except Exception as exc:
                raise CliError(f"转写音频分片 {chunk['id']} 失败：{exc}") from exc
            if not isinstance(result, dict):
                raise CliError(f"mlx_whisper 对 {chunk['id']} 返回了异常结构")
            detected = result.get("language")
            if isinstance(detected, str) and detected:
                detected_languages.append(detected)
            local_segments = normalize_mlx_segments(result, chunk["duration_ms"])
            atomic_write_json(
                checkpoint_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "signature": signature,
                    "detected_language": detected,
                    "segments": local_segments,
                },
            )
            transcribed += 1

        for local in local_segments:
            start = chunk["start_ms"] + int_ms(local.get("start_ms"), "checkpoint.start_ms")
            end = chunk["start_ms"] + int_ms(local.get("end_ms"), "checkpoint.end_ms")
            start = max(0, min(start, source_duration))
            end = max(0, min(end, source_duration))
            if end <= start:
                continue
            words: list[dict[str, Any]] = []
            raw_words = local.get("words", [])
            if isinstance(raw_words, list):
                for word in raw_words:
                    if not isinstance(word, dict):
                        continue
                    word_start = chunk["start_ms"] + int_ms(word.get("start_ms"), "checkpoint.word.start_ms")
                    word_end = chunk["start_ms"] + int_ms(word.get("end_ms"), "checkpoint.word.end_ms")
                    word_start = max(start, min(word_start, end))
                    word_end = max(start, min(word_end, end))
                    if word_end <= word_start:
                        continue
                    words.append({"start_ms": word_start, "end_ms": word_end, "text": str(word.get("text", ""))})
            global_segments.append(
                {
                    "start_ms": start,
                    "end_ms": end,
                    "text": str(local.get("text", "")).strip(),
                    "words": words,
                    "chunk_id": chunk["id"],
                }
            )

    global_segments = [segment for segment in global_segments if segment["text"]]
    global_segments.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["chunk_id"]))
    if not global_segments:
        raise CliError("所有分片均未识别到可用语音；未生成空 transcript")
    for index, segment in enumerate(global_segments, 1):
        segment["id"] = f"u{index:06d}"
    transcript = {
        "schema_version": SCHEMA_VERSION,
        "source_path": str(source),
        "duration_ms": source_duration,
        "language": language,
        "detected_languages": sorted(set(detected_languages)),
        "origin": {
            "type": "mlx-whisper",
            "model": model,
            "manifest": str(manifest_path),
            "model_cache": str(model_cache),
            "checkpoint_dir": str(checkpoint_dir),
        },
        "segments": global_segments,
    }
    # Apply the same strict canonical checks used by plan validation before the
    # transcript becomes an input to GPT.
    transcript_units(transcript, source_duration)
    atomic_write_json(output, transcript)
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(output),
                "segment_count": len(global_segments),
                "chunk_count": len(chunks),
                "chunks_transcribed": transcribed,
                "checkpoints_reused": reused,
                "checkpoint_dir": str(checkpoint_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


SRT_BLOCK_SPLIT = re.compile(r"\r?\n\s*\r?\n")
SRT_TIMECODE = re.compile(
    r"(?P<start>\d{1,3}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,3}:\d{2}:\d{2}[,.]\d{3})(?:\s+.*)?$"
)


def parse_srt_clock(value: str) -> int:
    match = re.fullmatch(r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        raise CliError(f"无效 SRT 时间码：{value}")
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    if minutes >= 60 or seconds >= 60:
        raise CliError(f"无效 SRT 时间码：{value}")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_srt(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="gb18030")
        except UnicodeDecodeError as exc:
            raise CliError("SRT 不是 UTF-8 或 GB18030 编码") from exc
    units: list[dict[str, Any]] = []
    for block_index, block in enumerate(SRT_BLOCK_SPLIT.split(text.strip()), 1):
        lines = [line.rstrip() for line in block.splitlines()]
        if not lines:
            continue
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if len(lines) < 2:
            raise CliError(f"SRT 第 {block_index} 块缺少时间码或正文")
        match = SRT_TIMECODE.fullmatch(lines[0].strip())
        if not match:
            raise CliError(f"SRT 第 {block_index} 块时间码无效：{lines[0]}")
        start = parse_srt_clock(match.group("start"))
        end = parse_srt_clock(match.group("end"))
        text_value = "\n".join(lines[1:]).strip()
        if start >= end or not text_value:
            raise CliError(f"SRT 第 {block_index} 块时长或正文无效")
        units.append(
            {
                "id": f"u{len(units) + 1:06d}",
                "start_ms": start,
                "end_ms": end,
                "text": text_value,
                "words": [],
            }
        )
    if not units:
        raise CliError("SRT 中没有可用字幕")
    units.sort(key=lambda item: (item["start_ms"], item["end_ms"]))
    return units


def command_import_srt(args: argparse.Namespace) -> int:
    srt_path = require_file(Path(args.srt), "SRT 文件")
    source = require_file(Path(args.source), "源视频")
    metadata = normalized_probe(source)
    units = parse_srt(srt_path)
    for unit in units:
        if unit["end_ms"] > metadata["duration_ms"]:
            raise CliError(f"字幕 {unit['id']} 结束时间超过源视频时长")
    transcript = {
        "schema_version": SCHEMA_VERSION,
        "source_path": str(source),
        "duration_ms": metadata["duration_ms"],
        "language": args.language,
        "origin": {"type": "srt", "path": str(srt_path)},
        "segments": units,
    }
    output = Path(args.out).expanduser().resolve()
    atomic_write_json(output, transcript)
    print(json.dumps({"ok": True, "out": str(output), "segment_count": len(units)}, ensure_ascii=False, indent=2))
    return 0


def packet_markdown(packet: dict[str, Any]) -> str:
    lines = [
        f"# 转写包 {packet['id']}",
        "",
        f"- 素材：{Path(str(packet.get('source_path', ''))).name}",
        f"- 源区间：{format_clock(packet['start_ms'])} - {format_clock(packet['end_ms'])}",
        f"- 单元数：{len(packet['segments'])}",
        "",
        "请只依据下列带时间码的源转写判断保留、删除、前后调序；不得补写原素材没有的事实或观点。",
        "",
    ]
    for unit in packet["segments"]:
        text_value = str(unit["text"]).replace("\n", " ").strip()
        lines.append(
            f"[{unit['id']}] {format_clock(unit['start_ms'])} --> {format_clock(unit['end_ms'])}  {text_value}"
        )
    lines.append("")
    return "\n".join(lines)


def command_packets(args: argparse.Namespace) -> int:
    transcript_path = Path(args.transcript).expanduser().resolve()
    transcript = load_json(transcript_path, "转写文件")
    duration = int_ms(transcript.get("duration_ms"), "transcript.duration_ms")
    units = transcript_units(transcript, duration)
    packet_minutes = float(args.packet_minutes)
    if not math.isfinite(packet_minutes) or packet_minutes <= 0:
        raise CliError("--packet-minutes 必须大于 0")
    packet_span = max(1, int(round(packet_minutes * 60_000)))

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_start = 0
    for unit in units:
        if current and unit["end_ms"] > current_start + packet_span:
            groups.append(current)
            current = []
        if not current:
            current_start = unit["start_ms"]
        current.append(unit)
    if current:
        groups.append(current)

    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise CliError(f"输出目录已存在，为避免覆盖已停止：{out_dir}")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=out_dir.parent))
    try:
        packet_index: list[dict[str, Any]] = []
        for index, group in enumerate(groups, 1):
            packet_id = f"p{index:04d}"
            packet = {
                "schema_version": SCHEMA_VERSION,
                "id": packet_id,
                "source_path": transcript["source_path"],
                "start_ms": group[0]["start_ms"],
                "end_ms": group[-1]["end_ms"],
                "segments": group,
            }
            json_name = f"packet_{index:04d}.json"
            markdown_name = f"packet_{index:04d}.md"
            atomic_write_json(temp_dir / json_name, packet)
            atomic_write_text(temp_dir / markdown_name, packet_markdown(packet))
            packet_index.append(
                {
                    "id": packet_id,
                    "start_ms": packet["start_ms"],
                    "end_ms": packet["end_ms"],
                    "segment_count": len(group),
                    "json": json_name,
                    "markdown": markdown_name,
                }
            )
        atomic_write_json(
            temp_dir / "index.json",
            {
                "schema_version": SCHEMA_VERSION,
                "source_path": transcript["source_path"],
                "packet_minutes": packet_minutes,
                "packets": packet_index,
            },
        )
        os.replace(temp_dir, out_dir)
        temp_dir = out_dir
    except BaseException:
        if temp_dir.exists() and temp_dir.name.startswith(f".{out_dir.name}."):
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    print(json.dumps({"ok": True, "out_dir": str(out_dir), "packet_count": len(groups)}, ensure_ascii=False, indent=2))
    return 0


def command_validate_plan(args: argparse.Namespace) -> int:
    validated = validate_inputs(Path(args.plan), Path(args.source_meta), Path(args.transcript))
    report = {
        "ok": True,
        "source_duration_ms": validated.source_duration_ms,
        "output_duration_ms": validated.output_duration_ms,
        "segment_count": len(validated.segments),
        "source_coverage_ratio": round(validated.output_duration_ms / validated.source_duration_ms, 6),
        "target_order": [segment["id"] for segment in validated.segments],
    }
    if args.out:
        atomic_write_json(Path(args.out), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def batch_validation_report(validated_batch: ValidatedBatch) -> dict[str, Any]:
    return {
        "ok": True,
        "kind": BATCH_PLAN_KIND,
        "batch_id": validated_batch.batch["batch_id"],
        "source_path": validated_batch.source_meta["source_path"],
        "source_duration_ms": validated_batch.source_meta["duration_ms"],
        "project_count": len(validated_batch.projects),
        "projects": [
            {
                "index": index,
                "id": item.project_id,
                "name": item.validated.plan["project"]["name"],
                "plan": str(item.plan_path),
                "segment_count": len(item.validated.segments),
                "output_duration_ms": item.validated.output_duration_ms,
                "needs_confirmation_count": sum(
                    segment.get("needs_confirmation") is True for segment in item.validated.segments
                ),
            }
            for index, item in enumerate(validated_batch.projects, 1)
        ],
    }


def command_validate_batch(args: argparse.Namespace) -> int:
    validated_batch = validate_batch_inputs(Path(args.batch))
    report = batch_validation_report(validated_batch)
    if args.out:
        atomic_write_json(Path(args.out), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def sanitize_filename(value: str) -> str:
    value = re.sub(r"[\x00-\x1f/:\\?*\"<>|]+", "_", value).strip(" ._")
    return value[:100] or "rough_cut"


def frame_duration_fraction(metadata: dict[str, Any]) -> Fraction:
    video = metadata.get("video")
    if isinstance(video, dict):
        numerator = video.get("fps_num")
        denominator = video.get("fps_den")
        if isinstance(numerator, int) and isinstance(denominator, int) and numerator > 0 and denominator > 0:
            return Fraction(denominator, numerator)
    return Fraction(1, 30)


def frame_duration(metadata: dict[str, Any]) -> str:
    return fcpxml_time(frame_duration_fraction(metadata))


def quantized_timeline(validated: ValidatedPlan, frame: Fraction | None = None) -> list[TimelineSegment]:
    frame = frame or frame_duration_fraction(validated.source_meta)
    if frame <= 0:
        raise CliError("无效的 FCPXML frameDuration")
    timeline: list[TimelineSegment] = []
    cursor = Fraction(0)
    for segment in validated.segments:
        requested_start = Fraction(segment["source_start_ms"], 1000)
        requested_end = Fraction(segment["source_end_ms"], 1000)
        source_start = floor_to_frame(requested_start, frame)
        source_end = ceil_to_frame(requested_end, frame)
        duration = source_end - source_start
        if duration <= 0:
            raise CliError(f"剪辑段 {segment['id']} 量化到帧后时长无效")
        timeline.append(
            TimelineSegment(
                segment=segment,
                source_start=source_start,
                source_end=source_end,
                duration=duration,
                offset=cursor,
            )
        )
        cursor += duration
    return timeline


def fcpxml_document(validated: ValidatedPlan, source: Path) -> str:
    metadata = validated.source_meta
    project = validated.plan["project"]
    frame = frame_duration_fraction(metadata)
    timeline = quantized_timeline(validated, frame)
    sequence_duration = sum((item.duration for item in timeline), Fraction(0))
    resources = ET.Element("resources")
    video = metadata.get("video") if isinstance(metadata.get("video"), dict) else None
    audio = metadata.get("audio") if isinstance(metadata.get("audio"), dict) else None

    format_attributes = {"id": "r1", "frameDuration": frame_duration(metadata)}
    if video:
        if isinstance(video.get("width"), int):
            format_attributes["width"] = str(video["width"])
        if isinstance(video.get("height"), int):
            format_attributes["height"] = str(video["height"])
    ET.SubElement(resources, "format", format_attributes)

    asset_attributes = {
        "id": "r2",
        "name": source.name,
        "src": source.as_uri(),
        "start": "0s",
        "duration": fcpxml_time(ceil_to_frame(Fraction(validated.source_duration_ms, 1000), frame)),
    }
    if video:
        asset_attributes.update({"hasVideo": "1", "format": "r1"})
    if audio:
        channel_count = audio.get("channels") if isinstance(audio.get("channels"), int) else 2
        sample_rate = audio.get("sample_rate") if isinstance(audio.get("sample_rate"), int) else 48000
        asset_attributes.update(
            {
                "hasAudio": "1",
                "audioSources": "1",
                "audioChannels": str(channel_count),
                "audioRate": str(sample_rate),
            }
        )
    ET.SubElement(resources, "asset", asset_attributes)

    root = ET.Element("fcpxml", {"version": FCPXML_VERSION})
    root.append(resources)
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": "长视频粗剪"})
    project_node = ET.SubElement(event, "project", {"name": project["name"].strip()})
    sequence_attributes = {
        "format": "r1",
        "duration": fcpxml_time(sequence_duration),
        "tcStart": "0s",
        "tcFormat": "NDF",
        "audioLayout": "stereo",
        "audioRate": "48k",
    }
    sequence = ET.SubElement(project_node, "sequence", sequence_attributes)
    spine = ET.SubElement(sequence, "spine")
    for item in timeline:
        segment = item.segment
        attributes = {
            "name": f"{segment['order']:03d}_{segment['id']}_{segment['role']}",
            "ref": "r2",
            "offset": fcpxml_time(item.offset),
            "start": fcpxml_time(item.source_start + Fraction(int(item.source_meta.get("start_ms") or 0),1000)),
            "duration": fcpxml_time(item.duration),
        }
        if audio:
            attributes["audioRole"] = "dialogue"
        ET.SubElement(spine, "asset-clip", attributes)

    try:
        ET.indent(root, space="  ")
    except AttributeError:  # pragma: no cover - Python 3.8 compatibility.
        pass
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n"


def words_for_intersection(unit: dict[str, Any], start: int, end: int) -> str:
    words = unit.get("words")
    if isinstance(words, list) and words:
        kept = [
            str(word["text"])
            for word in words
            if isinstance(word, dict)
            and int(word.get("end_ms", -1)) > start
            and int(word.get("start_ms", end)) < end
        ]
        if kept:
            # Whisper's word tokens normally carry their own leading spaces.
            return "".join(kept).strip()
    return str(unit.get("text", "")).strip()


def remapped_captions(validated: ValidatedPlan) -> list[dict[str, Any]]:
    captions: list[dict[str, Any]] = []
    for item in quantized_timeline(validated):
        segment = item.segment
        selected_units = [validated.units_by_id[unit_id] for unit_id in segment["evidence_unit_ids"]]
        selected_units.sort(key=lambda unit: (unit["start_ms"], unit["end_ms"], unit["id"]))
        for unit in selected_units:
            overlap_start = max(segment["source_start_ms"], unit["start_ms"])
            overlap_end = min(segment["source_end_ms"], unit["end_ms"])
            if overlap_end <= overlap_start:
                continue
            text_value = words_for_intersection(unit, overlap_start, overlap_end)
            if not text_value:
                continue
            target_start = item.offset + Fraction(overlap_start, 1000) - item.source_start
            target_end = item.offset + Fraction(overlap_end, 1000) - item.source_start
            captions.append(
                {
                    "start_ms": round_fraction_to_ms(target_start),
                    "end_ms": round_fraction_to_ms(target_end),
                    "text": text_value,
                    "source_unit_id": unit["id"],
                    "edit_segment_id": segment["id"],
                }
            )
    captions.sort(key=lambda caption: (caption["start_ms"], caption["end_ms"]))
    return captions


def srt_document(captions: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, caption in enumerate(captions, 1):
        text_value = str(caption["text"]).strip()
        blocks.append(
            f"{index}\n{format_clock(caption['start_ms'], srt=True)} --> "
            f"{format_clock(caption['end_ms'], srt=True)}\n{text_value}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def decisions_csv(validated: ValidatedPlan) -> str:
    output = io.StringIO(newline="")
    fields = [
        "order",
        "id",
        "role",
        "source_start",
        "source_end",
        "fcpxml_source_start",
        "fcpxml_source_end",
        "fcpxml_duration",
        "target_start",
        "target_end",
        "evidence_unit_ids",
        "reason",
        "transcript",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in quantized_timeline(validated):
        segment = item.segment
        writer.writerow(
            {
                "order": segment["order"],
                "id": segment["id"],
                "role": segment["role"],
                "source_start": format_clock(segment["source_start_ms"]),
                "source_end": format_clock(segment["source_end_ms"]),
                "fcpxml_source_start": fcpxml_time(item.source_start),
                "fcpxml_source_end": fcpxml_time(item.source_end),
                "fcpxml_duration": fcpxml_time(item.duration),
                "target_start": format_clock(round_fraction_to_ms(item.offset)),
                "target_end": format_clock(round_fraction_to_ms(item.offset + item.duration)),
                "evidence_unit_ids": " ".join(segment["evidence_unit_ids"]),
                "reason": segment["reason"],
                "transcript": segment["transcript"],
            }
        )
    return output.getvalue()


def markdown_cell(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def roughcut_report(
    validated: ValidatedPlan, fcpxml_path: Path, subtitle_path: Path, csv_path: Path
) -> str:
    project = validated.plan["project"]
    timeline = quantized_timeline(validated)
    actual_duration = sum((item.duration for item in timeline), Fraction(0))
    actual_duration_ms = round_fraction_to_ms(actual_duration)
    ratio = actual_duration_ms / validated.source_duration_ms
    lines = [
        f"# {project['name']}｜长视频粗剪交付报告",
        "",
        "## 项目定义",
        "",
        f"- 目的：{project['purpose']}",
        f"- 观众：{project['audience']}",
        f"- 核心观点：{project['core_view']}",
        f"- 购买理由：{project['buying_reason']}",
        f"- 情绪方向：{project['emotion']}",
        "",
        "## 交付摘要",
        "",
        f"- 原片：`{validated.source_meta['source_path']}`",
        f"- 原片时长：{format_clock(validated.source_duration_ms)}",
        f"- 粗剪时长（按帧量化）：{format_clock(actual_duration_ms)}（`{fcpxml_time(actual_duration)}`）",
        f"- 保留比例：{ratio:.1%}",
        f"- 保留段数：{len(validated.segments)}",
        f"- 剪映导入工程：`{fcpxml_path.name}`",
        f"- 同步字幕：`{subtitle_path.name}`",
        "",
        "> FCPXML 只引用原片绝对路径，不复制媒体。移动原片后需在剪映中重新链接；字幕以 SRT 单独导入。",
        "",
        "## 时间线决策",
        "",
        "| 顺序 | 段落 | 角色 | 原片入点 | 原片出点 | 粗剪入点 | 理由 |",
        "|---:|---|---|---:|---:|---:|---|",
    ]
    for item in timeline:
        segment = item.segment
        lines.append(
            "| {order} | {id} | {role} | {source_start} | {source_end} | {target_start} | {reason} |".format(
                order=segment["order"],
                id=markdown_cell(segment["id"]),
                role=markdown_cell(segment["role"]),
                source_start=format_clock(round_fraction_to_ms(item.source_start)),
                source_end=format_clock(round_fraction_to_ms(item.source_end)),
                target_start=format_clock(round_fraction_to_ms(item.offset)),
                reason=markdown_cell(segment["reason"]),
            )
        )
    confirmation_segments = [segment for segment in validated.segments if segment.get("needs_confirmation") is True]
    if confirmation_segments:
        lines.extend(["", "## 待人工确认", ""])
        lines.extend(
            f"- `{segment['id']}`：{segment['reason']}" for segment in confirmation_segments
        )
    lines.extend(
        [
            "",
            "## 人工精剪交接",
            "",
            "1. 保持原片路径不变，在剪映中导入 FCPXML。",
            "2. 导入同目录 SRT，抽查段首、段尾和调序连接处的字幕同步。",
            f"3. 依据 `{csv_path.name}` 和本报告复核删减理由，再进行包装、调色、音乐与画面补充。",
            "4. 最终发布前由人工复核事实、语义连续性、声音爆点和所有敏感信息。",
            "",
            "## 自动校验结论",
            "",
            "- 所有保留区间均在原片时长内。",
            "- 所有入点向下、出点向上量化到源视频帧边界，目标 offset 按量化后时长累计。",
            "- 所有剪辑段均引用真实转写单元，且计划原文可在证据中找到。",
            "- 输出顺序连续，非明确允许的源区间重叠已拦截。",
            "- FCPXML 媒体引用指向原片 `file://` URI，不含媒体副本。",
            "",
        ]
    )
    video = validated.source_meta.get("video")
    if isinstance(video, dict) and video.get("is_vfr") is True:
        lines.extend(
            [
                "## 可变帧率警告",
                "",
                "> 源素材被识别为 VFR。时间线使用名义帧率，必须抽查长片后半段音画同步；异常时先制作 CFR 代理用于复核。",
                "",
            ]
        )
    return "\n".join(lines)


def output_paths(
    out_value: str, project_name: str, force: bool = False
) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
    out = Path(out_value).expanduser().resolve()
    if out.suffix.lower() == ".fcpxml":
        out.parent.mkdir(parents=True, exist_ok=True)
        stem = out.stem
        paths = (
            out,
            out.with_suffix(".srt"),
            out.parent / f"{stem}_edit_decisions.csv",
            out.parent / f"{stem}_roughcut_report.md",
            out.parent / f"{stem}_edit_plan.json",
            out.parent / f"{stem}_transcript.json",
            out.parent / f"{stem}_source_meta.json",
        )
    else:
        if out.exists() and not out.is_dir():
            raise CliError(f"--out 必须是 .fcpxml 文件或交付目录：{out}")
        out.mkdir(parents=True, exist_ok=True)
        stem = sanitize_filename(project_name)
        paths = (
            out / f"{stem}.fcpxml",
            out / f"{stem}.srt",
            out / f"{stem}_edit_decisions.csv",
            out / f"{stem}_roughcut_report.md",
            out / f"{stem}_edit_plan.json",
            out / f"{stem}_transcript.json",
            out / f"{stem}_source_meta.json",
        )
    collisions = [path for path in paths if path.exists()]
    if collisions and not force:
        joined = "、".join(path.name for path in collisions)
        raise CliError(f"交付文件已存在（{joined}）；请换新目录，确需覆盖时传 --force")
    return paths


def compile_validated_plan(
    validated: ValidatedPlan, out_value: str, force: bool = False
) -> dict[str, Any]:
    source = require_file(Path(validated.source_meta["source_path"]), "源视频")
    timeline = quantized_timeline(validated)
    actual_duration = sum((item.duration for item in timeline), Fraction(0))
    fcpxml_path, subtitle_path, csv_path, report_path, plan_copy, transcript_copy, meta_copy = output_paths(
        out_value, validated.plan["project"]["name"], force=force
    )
    captions = remapped_captions(validated)
    atomic_write_text(fcpxml_path, fcpxml_document(validated, source))
    atomic_write_text(subtitle_path, srt_document(captions))
    atomic_write_text(csv_path, decisions_csv(validated))
    atomic_write_text(report_path, roughcut_report(validated, fcpxml_path, subtitle_path, csv_path))
    atomic_write_json(plan_copy, validated.plan)
    atomic_write_json(transcript_copy, validated.transcript)
    atomic_write_json(meta_copy, validated.source_meta)
    return {
        "ok": True,
        "fcpxml": str(fcpxml_path),
        "srt": str(subtitle_path),
        "edit_decisions_csv": str(csv_path),
        "roughcut_report": str(report_path),
        "edit_plan": str(plan_copy),
        "transcript": str(transcript_copy),
        "source_meta": str(meta_copy),
        "source_uri": source.as_uri(),
        "source_copied": False,
        "segment_count": len(validated.segments),
        "subtitle_count": len(captions),
        "output_duration": fcpxml_time(actual_duration),
        "output_duration_ms_rounded": round_fraction_to_ms(actual_duration),
        "frame_duration": frame_duration(validated.source_meta),
    }


def command_compile_fcpxml(args: argparse.Namespace) -> int:
    validated = validate_inputs(Path(args.plan), Path(args.source_meta), Path(args.transcript))
    result = compile_validated_plan(validated, args.out, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_manifest_entry(path: Path, root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise CliError(f"批量产物越过 staging 根目录：{resolved}") from exc
    if not resolved.is_file():
        raise CliError(f"批量产物缺失：{resolved}")
    return {
        "path": relative.as_posix(),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def command_compile_batch(args: argparse.Namespace) -> int:
    validated_batch = validate_batch_inputs(Path(args.batch))
    out_dir = Path(args.out).expanduser().resolve()
    if out_dir.suffix.lower() == ".fcpxml":
        raise CliError("compile-batch 的 --out 必须是新的批量交付目录，不能是 .fcpxml 文件")
    if out_dir.exists():
        raise CliError(f"批量交付目录已存在，拒绝覆盖：{out_dir}")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{out_dir.name}.",
            suffix=".staging",
            dir=out_dir.parent,
        )
    )
    published = False
    try:
        project_entries: list[dict[str, Any]] = []
        artifact_keys = (
            "fcpxml",
            "srt",
            "edit_decisions_csv",
            "roughcut_report",
            "edit_plan",
            "transcript",
            "source_meta",
        )
        for index, item in enumerate(validated_batch.projects, 1):
            delivery_dir_name = f"{index:02d}_{item.project_id}"
            delivery_dir = staging / delivery_dir_name
            result = compile_validated_plan(item.validated, str(delivery_dir), force=False)
            artifacts = {
                key: artifact_manifest_entry(Path(result[key]), staging) for key in artifact_keys
            }
            project_entries.append(
                {
                    "index": index,
                    "id": item.project_id,
                    "name": item.validated.plan["project"]["name"],
                    "plan_input": str(item.plan_path),
                    "delivery_dir": delivery_dir_name,
                    "segment_count": result["segment_count"],
                    "subtitle_count": result["subtitle_count"],
                    "output_duration": result["output_duration"],
                    "output_duration_ms_rounded": result["output_duration_ms_rounded"],
                    "frame_duration": result["frame_duration"],
                    "needs_confirmation_count": sum(
                        segment.get("needs_confirmation") is True
                        for segment in item.validated.segments
                    ),
                    "artifacts": artifacts,
                    "source_copied": False,
                }
            )

        delivery_batch_plan = dict(validated_batch.batch)
        delivery_batch_plan.update(
            {
                "source_meta": project_entries[0]["artifacts"]["source_meta"]["path"],
                "transcript": project_entries[0]["artifacts"]["transcript"]["path"],
                "recommended_count": len(project_entries),
                "projects": [
                    {
                        "id": entry["id"],
                        "plan": entry["artifacts"]["edit_plan"]["path"],
                    }
                    for entry in project_entries
                ],
            }
        )
        atomic_write_json(staging / "batch_plan.json", delivery_batch_plan)

        # Refuse to publish if the source changed while several projects were compiling.
        source = validate_source_fingerprint(validated_batch.source_meta)
        source_stat = source.stat()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": BATCH_DELIVERY_KIND,
            "batch_id": validated_batch.batch["batch_id"],
            "status": "complete",
            "fcpxml_version": FCPXML_VERSION,
            "source": {
                "path": str(source),
                "uri": source.as_uri(),
                "duration_ms": validated_batch.source_meta["duration_ms"],
                "size_bytes": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
                "copied": False,
            },
            "inputs": {
                "batch_plan": str(validated_batch.batch_path),
                "batch_plan_copy": "batch_plan.json",
                "source_meta": str(validated_batch.source_meta_path),
                "transcript": str(validated_batch.transcript_path),
            },
            "project_count": len(project_entries),
            "projects": project_entries,
        }
        atomic_write_json(staging / "batch_manifest.json", manifest)
        if out_dir.exists():
            raise CliError(f"发布前发现目标目录已出现，拒绝覆盖：{out_dir}")
        os.replace(staging, out_dir)
        published = True
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    final_manifest = out_dir / "batch_manifest.json"
    stdout_projects = [
        {
            "index": entry["index"],
            "id": entry["id"],
            "name": entry["name"],
            "fcpxml": str(out_dir / entry["artifacts"]["fcpxml"]["path"]),
        }
        for entry in project_entries
    ]
    print(
        json.dumps(
            {
                "ok": published,
                "out_dir": str(out_dir),
                "manifest": str(final_manifest),
                "project_count": len(stdout_projects),
                "projects": stdout_projects,
                "source_copied": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_verify_fcpxml(args: argparse.Namespace) -> int:
    fcpxml_path = require_file(Path(args.fcpxml), "FCPXML 文件")
    source = require_file(Path(args.source), "源视频")
    plan = load_json(Path(args.plan), "剪辑计划")
    ensure_schema_version(plan, "plan")
    source_meta = normalized_probe(source)
    plan_source = plan.get("source")
    if not isinstance(plan_source, dict):
        raise CliError("plan.source 必须是对象")
    if not same_path(plan_source.get("path"), str(source)):
        raise CliError("plan.source.path 与 --source 不一致")
    if int_ms(plan_source.get("duration_ms"), "plan.source.duration_ms") != source_meta["duration_ms"]:
        raise CliError("plan.source.duration_ms 与实际原片时长不一致")
    raw_segments = plan.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise CliError("plan.segments 必须是非空数组")
    normalized_segments: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    for index, raw in enumerate(raw_segments, 1):
        if not isinstance(raw, dict):
            raise CliError(f"plan.segments[{index}] 必须是对象")
        segment_id = raw.get("id")
        if not isinstance(segment_id, str) or not segment_id or segment_id in seen_ids:
            raise CliError(f"plan.segments[{index}].id 必须是唯一非空字符串")
        seen_ids.add(segment_id)
        order = int_ms(raw.get("order"), f"{segment_id}.order")
        if order <= 0 or order in seen_orders:
            raise CliError(f"剪辑段 {segment_id}.order 必须是唯一正整数")
        seen_orders.add(order)
        start = int_ms(raw.get("source_start_ms"), f"{segment_id}.source_start_ms")
        end = int_ms(raw.get("source_end_ms"), f"{segment_id}.source_end_ms")
        if not 0 <= start < end <= source_meta["duration_ms"]:
            raise CliError(f"剪辑段 {segment_id} 越界：{start}-{end}ms")
        segment = dict(raw)
        segment.update({"id": segment_id, "order": order, "source_start_ms": start, "source_end_ms": end})
        normalized_segments.append(segment)
    if sorted(seen_orders) != list(range(1, len(normalized_segments) + 1)):
        raise CliError(f"plan.segments.order 必须连续为 1..{len(normalized_segments)}")
    try:
        tree = ET.parse(fcpxml_path)
    except ET.ParseError as exc:
        raise CliError(f"FCPXML XML 解析失败：{exc}") from exc
    root = tree.getroot()
    dtd_validated = False
    if args.dtd:
        dtd_path = require_file(Path(args.dtd), "FCPXML 1.8 DTD")
        run_command(
            ["xmllint", "--noout", "--dtdvalid", str(dtd_path), str(fcpxml_path)],
            "FCPXML DTD 校验",
            timeout=120,
        )
        dtd_validated = True
    if root.tag != "fcpxml" or root.get("version") != FCPXML_VERSION:
        raise CliError(f"FCPXML 根节点或版本无效，应为 fcpxml {FCPXML_VERSION}")
    assets = root.findall("./resources/asset")
    if len(assets) != 1:
        raise CliError("FCPXML 必须且只能有一个原片 asset")
    expected_uri = source.resolve().as_uri()
    if assets[0].get("src") != expected_uri:
        raise CliError("FCPXML 的媒体 URI 与 --source 不一致")
    if assets[0].find("./media-rep") is not None:
        raise CliError("FCPXML 1.8 应直接使用 asset@src，不应包含 media-rep")
    format_node = root.find("./resources/format")
    if format_node is None:
        raise CliError("FCPXML 缺少 format")
    frame = parse_fcpxml_time(format_node.get("frameDuration", ""))
    expected_frame = frame_duration_fraction(source_meta)
    if frame != expected_frame:
        raise CliError(
            f"FCPXML frameDuration {fcpxml_time(frame)} 与原片 {fcpxml_time(expected_frame)} 不一致"
        )
    expected_asset_duration = ceil_to_frame(Fraction(source_meta["duration_ms"], 1000), frame)
    if parse_fcpxml_time(assets[0].get("duration", "")) != expected_asset_duration:
        raise CliError("asset.duration 未按帧覆盖实际原片时长")
    clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    ordered = sorted(normalized_segments, key=lambda segment: segment["order"])
    if len(clips) != len(ordered):
        raise CliError(f"FCPXML clip 数 {len(clips)} 与计划段数 {len(ordered)} 不一致")
    cursor = Fraction(0)
    clip_results: list[dict[str, Any]] = []
    for index, (clip, segment) in enumerate(zip(clips, ordered), 1):
        segment_id = segment.get("id", f"#{index}")
        start = int_ms(segment.get("source_start_ms"), f"{segment_id}.source_start_ms")
        end = int_ms(segment.get("source_end_ms"), f"{segment_id}.source_end_ms")
        expected_start = floor_to_frame(Fraction(start, 1000), frame)
        expected_end = ceil_to_frame(Fraction(end, 1000), frame)
        expected_duration = expected_end - expected_start
        actual_start = parse_fcpxml_time(clip.get("start", ""))
        actual_duration = parse_fcpxml_time(clip.get("duration", ""))
        actual_offset = parse_fcpxml_time(clip.get("offset", ""))
        if clip.get("ref") != assets[0].get("id"):
            raise CliError(f"clip {index} 没有引用唯一原片 asset")
        if (actual_start, actual_duration, actual_offset) != (expected_start, expected_duration, cursor):
            raise CliError(
                f"clip {index} 与计划不一致：实际 start/duration/offset="
                f"{actual_start}/{actual_duration}/{actual_offset}s"
            )
        clip_results.append(
            {
                "order": index,
                "id": segment_id,
                "start": fcpxml_time(expected_start),
                "duration": fcpxml_time(expected_duration),
                "offset": fcpxml_time(cursor),
                "start_ms_rounded": round_fraction_to_ms(expected_start),
                "duration_ms_rounded": round_fraction_to_ms(expected_duration),
                "offset_ms_rounded": round_fraction_to_ms(cursor),
            }
        )
        cursor += expected_duration
    sequence = root.find("./library/event/project/sequence")
    if sequence is None:
        raise CliError("FCPXML 缺少 sequence")
    sequence_duration = parse_fcpxml_time(sequence.get("duration", ""))
    if sequence_duration != cursor:
        raise CliError("sequence.duration 与所有 clips 的总时长不一致")

    report = {
        "ok": True,
        "fcpxml": str(fcpxml_path),
        "version": FCPXML_VERSION,
        "source_uri": expected_uri,
        "source_exists": True,
        "source_copied": False,
        "dtd_validated": dtd_validated,
        "clip_count": len(clips),
        "duration": fcpxml_time(cursor),
        "duration_ms_rounded": round_fraction_to_ms(cursor),
        "clips": clip_results,
    }
    if args.out:
        atomic_write_json(Path(args.out), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0



# =====================================================================
# 扩展（cy-edit 剪辑引擎）：按需安装、跨平台转写、整理素材、声音地图、多素材拼接、自动压停顿、字幕与包装、复查、反馈沉淀
# 以上为原长视频粗剪工具，未改动其校验和编译逻辑。
# =====================================================================

PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
HF_MIRROR = "https://hf-mirror.com"
MULTI_PLAN_KIND = "jianying_multisource_plan"
SKILL_ROOT = Path(__file__).resolve().parent.parent
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".webm", ".flv", ".wmv", ".3gp"}
MLX_MODELS = {"tiny": "mlx-community/whisper-tiny-mlx", "small": "mlx-community/whisper-small-mlx",
              "medium": "mlx-community/whisper-medium-mlx", "large-v3": "mlx-community/whisper-large-v3-mlx",
              "large-v3-turbo": "mlx-community/whisper-large-v3-turbo", "turbo": "mlx-community/whisper-large-v3-turbo"}


def user_data_dir() -> Path:
    """本机数据（专用环境、经验库）放在用户数据目录，Skill 升级或重装不会覆盖。可用 CY_EDIT_SHORTVIDEO_DATA 指定。"""
    if os.environ.get("CY_EDIT_DATA"):
        return Path(os.environ["CY_EDIT_DATA"]).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "cy-edit"
    return Path.home() / ".cy-edit"


# ---------------------------------------------------------------- 按需安装
def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _ffmpeg_dirs() -> list[Path]:
    dirs = [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin"), Path.home() / ".local" / "bin"]
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        dirs.insert(0, local / "Microsoft" / "WinGet" / "Links")
        dirs += [p.parent for p in (local / "Microsoft" / "WinGet" / "Packages").glob("*FFmpeg*/**/bin/ffprobe.exe")]
        dirs += [Path(r"C:\ffmpeg\bin"), Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin"]
    return dirs


def _locate_ffmpeg() -> bool:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True
    for directory in _ffmpeg_dirs():
        if (directory / _exe("ffmpeg")).is_file() and (directory / _exe("ffprobe")).is_file():
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
            return True
    return False


def ensure_ffmpeg():
    if _locate_ffmpeg():return
    # User-level wheels: no Homebrew, winget, administrator or system Python needed.
    imageio=ensure_python_module('imageio_ffmpeg','imageio-ffmpeg==0.6.0')
    ensure_python_module('av','av==16.0.1')
    directory=user_data_dir()/'bin';directory.mkdir(parents=True,exist_ok=True)
    target=directory/_exe('ffmpeg');origin=Path(imageio.get_ffmpeg_exe())
    if not target.exists():shutil.copyfile(origin,target);target.chmod(0o755)
    os.environ['PATH']=str(directory)+os.pathsep+os.environ.get('PATH','')
    if not shutil.which('ffmpeg'):raise CliError('独立 FFmpeg 准备失败，请检查用户目录写入权限')


def _editor_venv() -> Path:
    return user_data_dir() / "venv"


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _find_uv() -> str | None:
    for cand in (shutil.which("uv"), str(Path.home() / ".local" / "bin" / _exe("uv")), str(Path.home() / ".cargo" / "bin" / _exe("uv"))):
        if cand and Path(cand).is_file():
            return cand
    return None


def _rerun_in(python: Path) -> None:
    print(f"改用专用环境重新运行：{python}", file=sys.stderr)
    raise SystemExit(subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]]))


def _pip_into(python: Path, pip_name: str) -> bool:
    uv = _find_uv()
    base = [uv, "pip", "install", "--python", str(python), pip_name] if uv else [str(python), "-m", "pip", "install", pip_name]
    return any(subprocess.run(base + extra).returncode == 0 for extra in ([], ["-i", PIP_MIRROR]))


def ensure_python_module(module: str, pip_name: str) -> Any:
    """缺 Python 库时当场安装，只装进本 Skill 的专用环境（用户数据目录/venv，Python 3.12），不碰用户自己的 Python。
    当前不在专用环境里运行时：建好专用环境、装进去，再用它重跑刚才的命令。国外源失败自动改用清华镜像。"""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError:
        pass
    venv = _editor_venv()
    py = _venv_python(venv)
    in_venv = Path(sys.prefix).resolve() == venv.resolve()
    print(f"正在配置：第一次转写，自动安装 {pip_name}（只装这一次，约几分钟）…", file=sys.stderr)
    if in_venv:
        if _pip_into(Path(sys.executable), pip_name):
            importlib.invalidate_caches()
            return importlib.import_module(module)
    else:
        if not py.is_file():
            uv = _find_uv()
            made = bool(uv) and subprocess.run([uv, "venv", str(venv), "--python", "3.12", "--seed"]).returncode == 0
            if not made and (3, 9) <= sys.version_info[:2] <= (3, 12):
                subprocess.run([sys.executable, "-m", "venv", str(venv)])
        if py.is_file():
            ok = subprocess.run([str(py), "-c", f"import {module}"], capture_output=True).returncode == 0 or _pip_into(py, pip_name)
            if ok:
                _rerun_in(py)
    raise CliError(
        f"自动安装 {pip_name} 没成功（多半是网络问题，看上面的报错）。可以：\n"
        "  1. 换个网络后用 Skill 的启动入口（scripts/run.sh 或 scripts\\run.cmd）重跑；或\n"
        "  2. 完全不装转写：在剪映里用「识别字幕」生成字幕并导出 SRT，和视频同名放进素材文件夹，prepare 会直接导入。"
    )


def _hf_reachable(timeout: float = 5.0) -> bool:
    import urllib.request

    try:
        urllib.request.urlopen(urllib.request.Request("https://huggingface.co", method="HEAD"), timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def _prepare_model_download() -> None:
    """模型不预下载；第一次转写时由转写库自动下载并缓存。国外源连不上时自动改用国内镜像。"""
    if not os.environ.get("HF_ENDPOINT") and not _hf_reachable():
        os.environ["HF_ENDPOINT"] = HF_MIRROR
        print(f"Hugging Face 连不上，模型改从国内镜像 {HF_MIRROR} 下载", file=sys.stderr)


def pick_backend(requested: str | None) -> str:
    if requested and requested != "auto":
        return requested
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "mlx"
    return "faster-whisper"


def default_model(backend: str, requested: str | None) -> str:
    """同一个别名（small / large-v3-turbo …）在两个后端映射到各自正确的模型。"""
    name = (requested or "large-v3-turbo").strip()
    if backend == "mlx":
        return MLX_MODELS.get(name, name)
    reverse = {v: k for k, v in MLX_MODELS.items() if k != "turbo"}
    return reverse.get(name, "large-v3-turbo" if name == "turbo" else name)


class _FasterWhisperAdapter:
    """把 faster-whisper 包成和 mlx_whisper.transcribe 一样的返回结构。Windows / Intel Mac / Linux 用它。"""

    def __init__(self, model_name: str, model_cache: Path | None):
        fw = ensure_python_module("faster_whisper", "faster-whisper")
        kwargs: dict[str, Any] = {}
        if model_cache:
            kwargs["download_root"] = str(model_cache / "faster-whisper")
        cuda = False
        try:
            import ctranslate2  # type: ignore[import-not-found]

            cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:  # noqa: BLE001
            pass
        self.model = None
        if cuda:
            try:
                self.model = fw.WhisperModel(model_name, device="cuda", compute_type="float16", **kwargs)
                print(f"转写：faster-whisper {model_name}，显卡", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001  显卡驱动或 CUDA/cuDNN 不全时退回 CPU
                print(f"显卡转写不可用（{str(exc)[:120]}），改用 CPU", file=sys.stderr)
        if self.model is None:
            self.model = fw.WhisperModel(model_name, device="cpu", compute_type="int8", **kwargs)
            print(f"转写：faster-whisper {model_name}，CPU（第一次会自动下载模型）", file=sys.stderr)

    def transcribe(self, path: str, language: str | None, initial_prompt: str | None) -> dict[str, Any]:
        # 不延续上一段文字（防重复循环）；VAD 只去掉 1 秒以上的无声，免得静音里冒出“点赞订阅”之类的幻觉，又不吞短促的语气词。
        segments, info = self.model.transcribe(path, language=language, word_timestamps=True, initial_prompt=initial_prompt,
                                               condition_on_previous_text=False, vad_filter=True,
                                               vad_parameters={"min_silence_duration_ms": 1000})
        out = [{"start": s.start, "end": s.end, "text": s.text,
                "words": [{"start": w.start, "end": w.end, "word": w.word} for w in (s.words or [])]} for s in segments]
        return {"language": info.language, "segments": out}


def command_transcribe_any(args: argparse.Namespace) -> int:
    """跨平台转写：Apple 芯片 Mac 用 mlx-whisper，其他用 faster-whisper；缺库、缺模型时自动装。"""
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_json(manifest_path, "音频 manifest")
    source, source_duration, chunks = validate_audio_manifest(manifest)
    backend = pick_backend(args.backend)
    model = default_model(backend, args.model)
    language = str(args.language).strip() or "zh"
    lang_arg = None if language.lower() == "auto" else language
    prompt = args.initial_prompt
    if language.lower().startswith("zh"):
        # 引导转写照实记录口语里的语气词和重复，不要洗成书面句子
        prompt = ("嗯，那个，我我我们今天就是，就是说一下这个，呃，这个事情。以下是普通话口语，照实记录语气词和重复，使用简体中文。"
                  + (f"可能出现的词：{prompt}。" if prompt else ""))
    model_cache = Path(args.model_cache).expanduser().resolve() if args.model_cache else None
    if model_cache:
        model_cache.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(model_cache)
        os.environ["HF_HUB_CACHE"] = str(model_cache / "hub")
    output = Path(args.out).expanduser().resolve()
    job = json.dumps({"source": str(source), "model": model, "language": language, "backend": backend, "prompt": prompt},
                     ensure_ascii=False, sort_keys=True).encode("utf-8")
    checkpoint_dir = output.parent / ".transcribe_checkpoints" / hashlib.sha256(job).hexdigest()[:16]
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    engine: Any = None
    global_segments: list[dict[str, Any]] = []
    reused = transcribed = 0
    detected_languages: list[str] = []
    for index, chunk in enumerate(chunks, 1):
        signature = checkpoint_signature(chunk, f"{backend}:{model}:{hashlib.sha256(job).hexdigest()[:8]}", language)
        checkpoint_path = checkpoint_dir / f"{chunk['id']}.json"
        checkpoint = read_reusable_checkpoint(checkpoint_path, signature)
        if checkpoint is not None:
            local_segments, checkpoint_language = checkpoint
            if checkpoint_language:
                detected_languages.append(checkpoint_language)
            reused += 1
        else:
            if engine is None:
                _prepare_model_download()
                engine = ensure_python_module("mlx_whisper", "mlx-whisper") if backend == "mlx" else _FasterWhisperAdapter(model, model_cache)
            print(f"正在识别：{source.name} 第 {index}/{len(chunks)} 段", file=sys.stderr)
            try:
                if backend == "mlx":
                    result = engine.transcribe(chunk["path"], path_or_hf_repo=model, language=lang_arg,
                                               word_timestamps=True, initial_prompt=prompt,
                                               condition_on_previous_text=False)
                else:
                    result = engine.transcribe(chunk["path"], lang_arg, prompt)
            except Exception as exc:  # noqa: BLE001
                raise CliError(f"转写 {source.name} 第 {index} 段失败：{exc}") from exc
            detected = result.get("language")
            if isinstance(detected, str) and detected:
                detected_languages.append(detected)
            local_segments = normalize_mlx_segments(result, chunk["duration_ms"])
            atomic_write_json(checkpoint_path, {"schema_version": SCHEMA_VERSION, "signature": signature,
                                                "detected_language": detected, "segments": local_segments})
            transcribed += 1
        for local in local_segments:
            start = max(0, min(chunk["start_ms"] + int_ms(local.get("start_ms"), "checkpoint.start_ms"), source_duration))
            end = max(0, min(chunk["start_ms"] + int_ms(local.get("end_ms"), "checkpoint.end_ms"), source_duration))
            if end <= start:
                continue
            words = []
            for word in local.get("words", []) or []:
                ws = max(start, min(chunk["start_ms"] + int_ms(word.get("start_ms"), "word.start_ms"), end))
                we = max(start, min(chunk["start_ms"] + int_ms(word.get("end_ms"), "word.end_ms"), end))
                if we > ws:
                    words.append({"start_ms": ws, "end_ms": we, "text": str(word.get("text", ""))})
            global_segments.append({"start_ms": start, "end_ms": end, "text": str(local.get("text", "")).strip(),
                                    "words": words, "chunk_id": chunk["id"]})
    global_segments = [s for s in global_segments if s["text"]]
    global_segments.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["chunk_id"]))
    if not global_segments:
        raise CliError(f"{source.name} 没有识别到语音")
    for index, segment in enumerate(global_segments, 1):
        segment["id"] = f"u{index:06d}"
    transcript = {
        "schema_version": SCHEMA_VERSION, "source_path": str(source), "duration_ms": source_duration,
        "language": language, "detected_languages": sorted(set(detected_languages)),
        "origin": {"type": backend, "model": model, "prompt": prompt, "manifest": str(manifest_path),
                   "checkpoint_dir": str(checkpoint_dir)},
        "segments": global_segments,
    }
    transcript_units(transcript, source_duration)
    atomic_write_json(output, transcript)
    print(json.dumps({"ok": True, "out": str(output), "backend": backend, "model": model, "segment_count": len(global_segments),
                      "chunks_transcribed": transcribed, "checkpoints_reused": reused}, ensure_ascii=False, indent=2))
    return 0



# ---------------------------------------------------------------- 声音地图：哪里有声、哪里静音（不依赖转写）
ACOUSTIC_VERSION = 4
# 审阅包缓存版本（prepare 用来判断 packets 是否要重建；此前漏了定义，导致 prepare 在切审阅包这一步报 NameError）
PACKETS_VERSION = 1


def acoustic_map(path: Path) -> dict[str, Any]:
    """用 ffmpeg 一遍量出每 10 毫秒的音量，阈值按这条素材自己的底噪和说话音量自适应，得出静音区间。
    rms 留着给剪点找「声音最轻的那一刻」。10 分钟素材约 5 秒。"""
    stats = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
         "aformat=channel_layouts=mono,aresample=16000,asetnsamples=n=160:p=0,astats=metadata=1:reset=1,"
         "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace").stdout
    rms: list[int] = []
    for line in stats.splitlines():
        if "RMS_level=" in line:
            v = line.split("=", 1)[1].strip()
            rms.append(-100 if v in ("-inf", "inf", "nan") else max(-100, int(round(float(v)))))
    if len(rms) < 50:
        raise CliError(f"量不出 {path.name} 的音量（没有声音或无法解码）")
    s = sorted(rms)
    floor, loud = s[len(s) // 10], s[len(s) * 9 // 10]
    threshold = round(max(min(floor + 0.4 * (loud - floor), -25.0), -60.0), 1)
    silences, start = [], None
    for i, v in enumerate(rms + [0]):
        if v < threshold:
            start = i if start is None else start
        else:
            if start is not None and i - start >= 20:
                silences.append([start * 10, i * 10])
            start = None
    return {"version": ACOUSTIC_VERSION, "threshold_db": threshold, "floor_db": floor, "speech_db": loud,
            "silences_ms": silences, "rms_db_10ms": rms, "auto_trim_reliable": loud-floor>=10 and loud>-45}


def quietest(rms: list[int] | None, lo: int, hi: int, default: int) -> int:
    """在 [lo, hi] 毫秒里找声音最轻的那一刻（取最轻的一段的中间），找不到就用 default。"""
    if not rms or hi - lo < 20:
        return default
    a, b = max(0, lo // 10), min(len(rms) - 1, hi // 10)
    if b <= a:
        return default
    low = min(rms[a:b + 1])
    best, run, best_run = a, None, (0, a)
    for i in range(a, b + 1):
        if rms[i] <= low + 2:
            run = i if run is None else run
            if i - run + 1 > best_run[0]:
                best_run = (i - run + 1, run)
        else:
            run = None
    best = best_run[1] + best_run[0] // 2
    return best * 10


DEFAULT_PAUSES = {"keep_max_ms": 600, "tail_ms": 200, "head_ms": 150}


def tighten_ranges(start, end, silences, cuts=None, pauses=None, keep_pauses=False, words=None, rms=None):
    from cy_quality import safe_ranges
    policy = dict(DEFAULT_PAUSES, **(pauses or {}))
    try:
        parts = safe_ranges(start/1000, end/1000, [(a/1000,b/1000) for a,b in silences],
                            [(a/1000,b/1000) for a,b in (words or [])],
                            [(a/1000,b/1000) for a,b in (cuts or [])], rms, .01,
                            not keep_pauses, policy['keep_max_ms']/1000, policy['tail_ms']/1000, policy['head_ms']/1000)
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    return [[round(a*1000),round(b*1000)] for a,b in parts]

def _char_times(unit):
    # Characters share their enclosing token bounds. These are NOT character timestamps.
    return [(c,int(w['start_ms']),int(w['end_ms'])) for w in unit.get('words',[])
            for c in str(w['text']) if c.isalnum()]

def _habits(unit):
    from cy_quality import habits
    words=[{'text':w['text'],'start':w['start_ms']/1000,'end':w['end_ms']/1000} for w in unit.get('words',[])]
    return [(h['text'],round(h['start']*1000),round(h['end']*1000),h['candidate']) for h in habits(words)]

# 审阅包标记用的基础词表（此前漏了定义，_unit_flags 一调用就 NameError；只影响提示，不自动删除）
FILLERS = {'嗯', '呃', '唔', '额', '嗯嗯'}
REDUP_OK = set('看看试试聊聊说说想想走走坐坐听听问问笑笑玩玩逛逛歇歇躺躺拍拍念念读读写写瞧瞧转转摸摸碰碰躲躲慢慢好好刚刚天天人人个个点点常常往往渐渐稍稍偷偷暗暗明明白白双双对对多多久久生生家家谢谢等等边边步步条条句句份份层层遍遍')
EMPHASIS_OK = {'非常非常', '特别特别', '很多很多', '好久好久', '很久很久', '一次一次', '一遍一遍', '一点一点',
               '一天一天', '一年一年', '一步一步', '一个一个', '一句一句', '一层一层'}


def _unit_flags(unit: dict[str, Any], nxt: dict[str, Any] | None, silences: list[list[int]]) -> list[str]:
    flags = []
    a, b = unit["start_ms"], unit["end_ms"]
    quiet = sum(max(0, min(b, y) - max(a, x)) for x, y in silences)
    if b - a >= 500 and quiet >= 0.8 * (b - a):
        flags.append("⚠ 文字落在低能量区，可能是轻声或识别误差，回听核实")
    for x, y in silences:
        if x > a + 50 and y < b - 50 and y - x >= 500:
            flags.append(f"⏸ 句中停顿 {(y - x) / 1000:.1f} 秒（{format_clock(x)}）")
    for w in unit.get("words") or []:
        if str(w["text"]).strip(" ，。,.!?！？、") in FILLERS:
            flags.append(f"✂ 语气词「{str(w['text']).strip()}」→ cut [{w['start_ms']}, {w['end_ms']}]")
    chars = _char_times(unit)
    s = "".join(c for c, _, _ in chars)
    i, seen = 0, set()
    while i < len(s):
        hit = False
        for k in (4, 3, 2, 1):
            if i + 2 * k <= len(s) and s[i:i + k] == s[i + k:i + 2 * k]:
                rep = s[i:i + 2 * k]
                if (k == 1 and s[i] in REDUP_OK) or rep in EMPHASIS_OK or rep in seen:
                    continue
                seen.add(rep)
                flags.append(f"↻ 疑似卡壳「{s[i:i + k]}…{s[i + k:i + 2 * k]}」（{chars[i][1]}ms 附近）；核对真实词边界再删，是强调则保留")
                i += k
                hit = True
                break
        if not hit:
            i += 1
    habits = [h for h in _habits(unit) if h[3]]
    if len(habits) >= 2:
        names = "、".join(h[0] for h in habits)
        cuts = " ".join(f"[{h[1]}, {h[2]}]" for h in habits)
        flags.append(f"✂ 口癖 {len(habits)} 个（{names}）→ 可 cut {cuts}（口癖密的地方删掉一部分，删到听着顺就停；说得顺的不删）")
    if chars:  # 有声音但没有识别出字：被转写吞掉的嗯、卡壳，或咳嗽、清嗓
        spans = [(c0, c1) for _, c0, c1 in chars]
        speech, cur = [], a
        for x, y in silences:
            if y <= a or x >= b:
                continue
            if x > cur:
                speech.append((cur, min(x, b)))
            cur = max(cur, y)
        if cur < b:
            speech.append((cur, b))
        for s0, s1 in speech:
            covered = [(max(s0, c0 - 80), min(s1, c1 + 80)) for c0, c1 in spans if c1 + 80 > s0 and c0 - 80 < s1]
            pos, gaps = s0, []
            for c0, c1 in sorted(covered):
                if c0 - pos >= 300:
                    gaps.append((pos, c0))
                pos = max(pos, c1)
            if s1 - pos >= 300:
                gaps.append((pos, s1))
            for g0, g1 in gaps:
                flags.append(f"❓ 有声音但没识别出字 {(g1 - g0) / 1000:.1f} 秒（{format_clock(g0)}），可能是嗯、卡壳或咳嗽 → 回听 [{g0}, {g1}]，不能仅因未识别文字就删除")
    if nxt is not None:
        p, q = normalized_text(unit["text"]), normalized_text(nxt["text"])
        common = 0
        while common < min(len(p), len(q)) and p[common] == q[common]:
            common += 1
        if common >= 4 and p != q:
            flags.append(f"↻ 疑似说一半重来：和下一句 {nxt['id']} 开头相同「{p[:common]}」，通常只留后一遍")
        elif p == q:
            flags.append(f"↻ 和下一句 {nxt['id']} 完全相同，是重录，留说得更好的一遍")
        gap = nxt["start_ms"] - b
        if gap >= 600:
            flags.append(f"⏸ 句后停顿 {gap / 1000:.1f} 秒")
    return flags


def write_review_packets(transcript: dict[str, Any], silences: list[list[int]], out_dir: Path, packet_minutes: float, key: str) -> int:
    units = transcript_units(transcript, int(transcript["duration_ms"]))
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    span = max(60_000, int(packet_minutes * 60_000))
    groups, cur, start = [], [], 0
    for u in units:
        if cur and u["end_ms"] > start + span:
            groups.append(cur)
            cur = []
        if not cur:
            start = u["start_ms"]
        cur.append(u)
    if cur:
        groups.append(cur)
    word_level = any(u.get("words") for u in units)
    index = {u["id"]: n for n, u in enumerate(units)}
    for gi, group in enumerate(groups, 1):
        lines = [f"# 素材 {key} · 审阅包 {gi}/{len(groups)}", "",
                 f"- 素材：{Path(str(transcript.get('source_path', ''))).name}；区间 {format_clock(group[0]['start_ms'])}–{format_clock(group[-1]['end_ms'])}",
                 f"- 时间精度：{'ASR 词级估计时间（多字词不能平分后当精确剪点）' if word_level else '只有整句时间（来自 SRT），只能整句删留'}", "",
                 "标记：⏸ 停顿（超过 0.6 秒的编译时自动压掉，词外可靠静音自动压缩；有声音的咳嗽、清嗓先回听再写 cut）；"
                 "✂ 可删的语气词、卡壳、口癖，把 cut 后面的区间抄进这一段的 \"cut\"；"
                 "↻ 重来或重录；❓ 有声音没识别出字；⚠ 低能量区疑似误识别，需核实。", ""]
        for u in group:
            n = index[u["id"]]
            nxt = units[n + 1] if n + 1 < len(units) else None
            lines.append(f"[{u['id']}] {format_clock(u['start_ms'])} → {format_clock(u['end_ms'])}  {' '.join(str(u['text']).split())}")
            lines += [f"    {f}" for f in _unit_flags(u, nxt, silences)]
        from cy_quality import dense_habits
        ws=[{'text':w['text'],'start':w['start_ms']/1000,'end':w['end_ms']/1000} for u in group for w in u.get('words',[])]
        for d in dense_habits(ws):
            lines.append(f"口癖密度提示：{format_clock(round(d['start']*1000))} 起 10 秒内 {d['count']} 次；正常指代和数量词不机械删除")
        lines.append("")
        atomic_write_text(out_dir / f"packet_{gi:04d}.md", "\n".join(lines))
    return len(groups)


# ---------------------------------------------------------------- prepare：一条命令整理整个素材文件夹
def _source_letters(count: int) -> list[str]:
    letters = []
    for i in range(count):
        name, n = "", i
        while True:
            name = chr(ord("A") + n % 26) + name
            n = n // 26 - 1
            if n < 0:
                break
        letters.append(name)
    return letters


def _file_stamp(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    st = path.stat()
    return {"path": str(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _rotation(path: Path) -> int:
    try:
        raw = ffprobe_raw(path)
    except CliError:
        return 0
    for s in raw.get("streams", []):
        if s.get("codec_type") != "video":
            continue
        rot = (s.get("tags") or {}).get("rotate")
        for sd in s.get("side_data_list") or []:
            rot = rot or sd.get("rotation")
        try:
            return int(float(rot or 0))
        except ValueError:
            return 0
    return 0


def _hotwords(sources_dir: Path, given: str | None) -> str | None:
    if given:
        return given
    need = sources_dir.parent / "需求.md"
    if need.is_file():
        for line in need.read_text(encoding="utf-8").splitlines():
            if "专有名词" in line and "：" in line:
                words = line.split("：", 1)[1].strip()
                return words or None
    return None


def _conform(video: Path, target: Fraction, out_dir: Path, key: str) -> Path:
    """生成派生的恒定帧率工作副本（原片不动）。已生成且原片没变就复用。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{key}_{fps_label(target)}fps{video.suffix if video.suffix.lower() in {'.mp4', '.mov'} else '.mp4'}"
    stamp_path = out.with_suffix(out.suffix + ".json")
    stamp = _file_stamp(video)
    if out.is_file() and stamp_path.is_file() and load_json(stamp_path, "派生副本记录").get("source") == stamp:
        return out
    print(f"正在生成恒定帧率副本：{video.name} → {out.name}（原片不动）", file=sys.stderr)
    run_command(["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-r", fps_label(target, True),
                 "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", str(out)], "生成恒定帧率副本", timeout=None)
    atomic_write_json(stamp_path, {"source": stamp, "fps": str(target)})
    return out


def fps_label(f: Fraction, exact: bool = False) -> str:
    if exact:
        return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"
    return f"{float(f):g}"


def command_prepare(args: argparse.Namespace) -> int:
    ensure_feedback_project(Path(args.workdir))
    from lve_feedback_flow import experience
    experience_result=experience(args.workdir, shared_experience_data(), "单主题口播清理")
    """素材文件夹里每条视频：编号 → 读参数 → 抽音频 → 转写（或导入同名 SRT）→ 切审阅包。
    可中断重跑：素材、同名 SRT、模型、语言、专有名词、帧率参数都没变的步骤自动跳过；变了的自动重做。"""
    sources_dir = Path(args.sources).expanduser().resolve()
    workdir = Path(args.workdir).expanduser().resolve()
    if not sources_dir.is_dir():
        raise CliError(f"素材文件夹不存在：{sources_dir}")
    videos = sorted((p for p in sources_dir.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES and not p.name.startswith(".")),
                    key=lambda p: p.name)
    if not videos:
        raise CliError(f"{sources_dir} 里没有视频文件")
    workdir.mkdir(parents=True, exist_ok=True)
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):  # 子步骤的输出只进日志，标准输出只留最后一份结果
        result = _prepare_body(args, sources_dir, workdir, videos)
    result["experience"]=experience_result
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _prepare_body(args: argparse.Namespace, sources_dir: Path, workdir: Path, videos: list[Path]) -> dict[str, Any]:
    registry_path = workdir / "sources.json"
    registry = load_json(registry_path, "素材登记") if registry_path.is_file() else {"schema_version": SCHEMA_VERSION, "sources": {}}
    current = {str(v) for v in videos}
    removed = {k: v for k, v in registry["sources"].items() if v["path"] not in current}
    for k in removed:
        registry["sources"].pop(k)
    by_path = {v["path"]: k for k, v in registry["sources"].items()}
    used = set(registry["sources"]) | set(removed)
    letters = iter(x for x in _source_letters(len(videos) + len(used) + 1) if x not in used)
    added = []
    for video in videos:
        if str(video) not in by_path:
            key = next(letters)
            registry["sources"][key] = {"path": str(video), "filename": video.name}
            added.append(key)
    # 中途换到专用环境重跑、或被打断时，本次增减不能丢：先记进登记表，清单写完再清掉
    pending = registry.get("pending_changes") or {"added": [], "removed": {}}
    pending["added"] = sorted(set(pending["added"]) | set(added))
    pending["removed"].update({k: v for k, v in removed.items()})
    registry["pending_changes"] = pending
    added = [k for k in pending["added"] if k in registry["sources"]]
    removed = {k: v for k, v in pending["removed"].items() if k not in registry["sources"]}
    atomic_write_json(registry_path, registry)

    hot = _hotwords(sources_dir, args.hotwords)
    backend = pick_backend(args.backend)
    model = default_model(backend, args.model)
    atomic_write_json(workdir/"asr-options.json",{"backend":backend,"model":args.model,"language":args.language})
    target_fps = Fraction(str(args.conform_fps)) if args.conform_fps else None
    fps_seen: dict[str, Fraction] = {}
    rows, warnings, redone = [], [], []
    for key, entry in sorted(registry["sources"].items()):
        video = Path(entry["path"])
        d = workdir / key
        d.mkdir(exist_ok=True)
        print(f"== 素材 {key}：{video.name}", file=sys.stderr)
        meta0 = normalized_probe(video)
        v0 = meta0.get("video") or {}
        fps0 = Fraction(v0["fps_num"], v0["fps_den"]) if v0.get("fps_num") else None
        working = video
        if target_fps and fps0 and (fps0 != target_fps or v0.get("is_vfr")):
            working = _conform(video, target_fps, workdir / "派生素材", key)
        entry["working_path"] = str(working)
        srt = video.with_suffix(".srt")
        cache_key = {"source": _file_stamp(video), "working": _file_stamp(working), "srt": _file_stamp(srt),
                     "backend": backend, "model": model, "language": args.language, "hotwords": hot}
        key_path = d / "cache_key.json"
        old_key = load_json(key_path, "缓存记录") if key_path.is_file() else None
        transcript_path = d / "transcript.json"
        if old_key != cache_key:
            if old_key is not None:
                redone.append(key)
            for stale in (transcript_path, d / "audio_manifest.json"):
                if stale.exists():
                    stale.unlink()
            for stale_dir in (d / "audio_chunks", d / "packets"):
                if stale_dir.exists():
                    shutil.rmtree(stale_dir)
        meta = normalized_probe(working)
        atomic_write_json(d / "source_meta.json", meta)
        v = meta.get("video") or {}
        if v.get("fps_num"):
            fps_seen[key] = Fraction(v["fps_num"], v["fps_den"])
        if not transcript_path.is_file():
            if srt.is_file():
                command_import_srt(argparse.Namespace(srt=str(srt), source=str(working), out=str(transcript_path), language=args.language))
            elif meta.get("audio"):
                manifest = d / "audio_manifest.json"
                command_extract_audio(argparse.Namespace(source=str(working), workdir=str(d), chunk_minutes=args.chunk_minutes,
                                                         manifest=str(manifest)))
                command_transcribe_any(argparse.Namespace(manifest=str(manifest), out=str(transcript_path), backend=backend,
                                                          model=args.model, model_cache=args.model_cache,
                                                          language=args.language, initial_prompt=hot))
        vad_path = d / "vad.json"
        vad_key = {"working": _file_stamp(working), "version": ACOUSTIC_VERSION}
        vad = load_json(vad_path, "声音地图") if vad_path.is_file() else {}
        if vad.get("key") != vad_key and meta.get("audio"):
            print("   测量停顿（声音地图）…", file=sys.stderr)
            vad = dict(acoustic_map(working), key=vad_key)
            atomic_write_json(vad_path, vad)
        if transcript_path.is_file():
            pk = {"transcript": hashlib.sha256(transcript_path.read_bytes()).hexdigest()[:16],
                  "vad": vad.get("threshold_db"), "version": PACKETS_VERSION, "minutes": args.packet_minutes}
            pk_path = d / "packets_key.json"
            if not (d / "packets").exists() or not pk_path.is_file() or load_json(pk_path, "审阅包记录") != pk:
                write_review_packets(load_json(transcript_path, "转写"), vad.get("silences_ms", []), d / "packets",
                                     args.packet_minutes, key)
                atomic_write_json(pk_path, pk)
        atomic_write_json(key_path, cache_key)
        if not meta.get("audio"):
            warnings.append(f"- {key}（{video.name}）没有声音，不能用于口播剪辑。")
        if (meta.get("audio_stream_count") or 0) > 1:
            warnings.append(f"- {key}（{video.name}）有 {meta['audio_stream_count']} 条音轨，转写和工程都用默认音轨；如果主声音在别的音轨，告诉用户。")
        rot = _rotation(working)
        if rot:
            warnings.append(f"- {key}（{video.name}）带 {rot}° 旋转信息，导入剪辑软件后检查画面方向；方向不对时用 --conform-fps 生成副本（会自动转正）。")
        if v.get("is_vfr"):
            warnings.append(f"- {key}（{video.name}）是可变帧率，导入后抽查后半段音画是否同步；不同步就加 --conform-fps 重跑。")
        srt_note = "同名 SRT（只有句级时间）" if srt.is_file() else ("转写" if meta.get("audio") else "—")
        derived = "（用派生副本）" if working != video else ""
        rows.append(f"| {key} | {video.name}{derived} | {format_clock(meta['duration_ms'])} | {v.get('width')}×{v.get('height')} | "
                    f"{fps_label(fps_seen[key]) if key in fps_seen else '—'} | {srt_note} | {key}/packets/ |")
    atomic_write_json(registry_path, registry)
    if len(set(fps_seen.values())) > 1:
        detail = "，".join(f"{k} {fps_label(f)}" for k, f in fps_seen.items())
        common = max(set(fps_seen.values()), key=list(fps_seen.values()).count)
        warnings.insert(0, f"- ⚠ 帧率不一致（{detail}），工程按首条的时间网格生成，各素材保留原帧率。仅在目标编辑器出现同步问题时，经用户同意运行 prepare 加 `--conform-fps {fps_label(common)}`："
                           "为少数派生成恒定帧率的派生副本（放在 工作/派生素材/，原片不动），工程改用副本。")
    md = ["# 素材清单", "", "| 编号 | 文件 | 时长 | 画面 | 帧率 | 文字来源 | 审阅包 |", "|---|---|---|---|---|---|---|", *rows, ""]
    if added or removed or redone:
        md += ["## 这次的变化", ""]
        added_txt = "，".join(k + "（" + registry["sources"][k]["filename"] + "）" for k in added)
        removed_txt = "，".join(k + "（" + v["filename"] + "）" for k, v in removed.items())
        md += ["- 新增：" + added_txt] if added else []
        md += ["- 已移除（素材文件夹里没有了）：" + removed_txt + "；剪辑计划里用到它们的段落要删掉"] if removed else []
        md += ["- 重新转写（素材、字幕或转写参数变了）：" + "，".join(redone) + "；旧剪辑计划要按新审阅包核对"] if redone else []
        md.append("")
    if warnings:
        md += ["## 需要注意", "", *warnings, ""]
    md += ["下一步：按编号读各素材的 packets/*.md，照 剪辑计划_模板.json 写 plan_v1.json（格式见 references/plan-format.md，标题和章节见 references/packaging.md）。"]
    atomic_write_text(workdir / "素材清单.md", "\n".join(md) + "\n")
    usable={k:{'source_meta':f'{k}/source_meta.json','transcript':f'{k}/transcript.json'} for k in sorted(registry['sources']) if (workdir/k/'transcript.json').is_file()}
    first_key=next(iter(usable),None);starter=[]
    if first_key:
        t=load_json(workdir/usable[first_key]['transcript'],'转写');us=transcript_units(t,int(t['duration_ms']))
        if us:starter=[{'source':first_key,'evidence_unit_ids':[us[0]['id']],'reason':'根据全片审阅结果更新本段取舍理由'}]
    template={'schema_version':SCHEMA_VERSION,'kind':MULTI_PLAN_KIND,'project':{'name':workdir.parent.name+'_v1'},'sources':usable,
              'content_title':['根据成片写话题','根据成片写结论'],'cover_title':['根据成片写封面','必须由内容兑现'],'chapters':[],'segments':starter}
    atomic_write_json(workdir / "剪辑计划_模板.json", template)
    registry.pop("pending_changes", None)
    atomic_write_json(registry_path, registry)
    return {"ok": True, "sources": {k: v["filename"] for k, v in registry["sources"].items()},
            "added": added, "removed": sorted(removed), "retranscribed": redone,
            "fps_mismatch": len(set(fps_seen.values())) > 1, "inventory": str(workdir / "素材清单.md"),
            "experience": None}


# ---------------------------------------------------------------- 多素材剪辑计划：校验与编译
@dataclass(frozen=True)
class MultiItem:
    key: str
    segment: dict[str, Any]
    source_path: Path
    source_meta: dict[str, Any]
    units_by_id: dict[str, dict[str, Any]]
    source_start: Fraction
    source_end: Fraction
    duration: Fraction
    offset: Fraction


def _strict_checks(key: str, seg: dict[str, Any], units: list[dict[str, Any]]) -> None:
    """在原工具校验之上再加三道：区间里所有原话都要列出；句级时间不能裁半句；剪点不能切在字中间。"""
    start, end = seg["source_start_ms"], seg["source_end_ms"]
    listed = set(seg["evidence_unit_ids"])
    for u in units:
        overlap = min(end, u["end_ms"]) - max(start, u["start_ms"])
        if overlap <= 0:
            continue
        span = u["end_ms"] - u["start_ms"]
        if u["id"] not in listed and overlap > 20:
            raise CliError(f"剪辑段 {seg['id']}（素材 {key}）的区间里还带着没列出的原话 {u['id']}「{' '.join(u['text'].split())[:20]}」："
                           "要保留就把它加进 evidence_unit_ids，不要就把区间收窄")
        if u["id"] not in listed:
            continue
        if not u.get("words") and (start > u["start_ms"] + 50 or end < u["end_ms"] - 50):
            raise CliError(f"剪辑段 {seg['id']}：{u['id']} 只有句级时间（来自 SRT），不能只保留半句；区间要完整包住这句")
        for w in u.get("words") or []:
            for cut, label in ((start, "起点"), (end, "终点")):
                if w["start_ms"] + 20 < cut < w["end_ms"] - 20:
                    raise CliError(f"剪辑段 {seg['id']} 的{label} {cut}ms 切在「{w['text'].strip()}」"
                                   f"（{w['start_ms']}–{w['end_ms']}ms）中间，会吞字；挪到这个字之前或之后")


def validate_multi(plan_path: Path) -> tuple[dict[str, Any], list[MultiItem], Fraction, dict[str, dict[str, Any]]]:
    plan_path = require_file(plan_path, "剪辑计划")
    plan = load_json(plan_path, "剪辑计划")
    ensure_schema_version(plan, "plan")
    if plan.get("kind") != MULTI_PLAN_KIND:
        raise CliError(f"plan.kind 必须为 {MULTI_PLAN_KIND!r}（照 剪辑计划_模板.json 写）")
    sources = plan.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise CliError("plan.sources 必须是 {编号: {source_meta, transcript}} 对象（照模板写）")
    raw_segments = plan.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise CliError("plan.segments 必须是非空数组")
    # Minimal plan: source + evidence_unit_ids + reason. Derive time and original wording.
    cache_units={};cache_durations={}
    for index,seg in enumerate(raw_segments,1):
        key=seg.get('source')
        if key not in sources:raise CliError('剪辑段 source 不在素材清单中')
        if key not in cache_units:
            t=load_json(resolve_batch_reference(plan_path,sources[key]['transcript'],'transcript'),'转写')
            cache_units[key]=transcript_units(t,int(t['duration_ms']));cache_durations[key]=int(t['duration_ms'])
        us=cache_units[key];byid={u['id']:u for u in us};ids=seg.get('evidence_unit_ids',[])
        if not ids or any(u not in byid for u in ids):raise CliError('请填写真实保留原文编号 evidence_unit_ids')
        chosen=[byid[u] for u in ids];a=min(u['start_ms'] for u in chosen);b=max(u['end_ms'] for u in chosen)
        other=[u for u in us if u['id'] not in ids]
        left=max([0]+[u['end_ms'] for u in other if u['end_ms']<=a]);right=min([cache_durations[key]]+[u['start_ms'] for u in other if u['start_ms']>=b])
        seg.setdefault('id',f's{index:03d}');seg.setdefault('order',index);seg.setdefault('source_start_ms',max(left,a-60));seg.setdefault('source_end_ms',min(right,b+80))
        seg.setdefault('role','正文');seg.setdefault('transcript',' '.join(u['text'] for u in chosen))
    orders = []
    for index, seg in enumerate(raw_segments, 1):
        if not isinstance(seg, dict) or seg.get("source") not in sources:
            raise CliError(f"plan.segments[{index}].source 必须是 sources 里的编号")
        orders.append(int_ms(seg.get("order"), f"segments[{index}].order"))
    if sorted(orders) != list(range(1, len(orders) + 1)):
        raise CliError(f"plan.segments.order 必须从 1 连续编号到 {len(orders)}")
    loaded: dict[str, dict[str, Any]] = {}
    validated_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    for key, ref in sources.items():
        meta = load_json(resolve_batch_reference(plan_path, ref.get("source_meta"), f"sources.{key}.source_meta"), "媒体元数据")
        transcript = load_json(resolve_batch_reference(plan_path, ref.get("transcript"), f"sources.{key}.transcript"), "转写文件")
        mine = sorted((s for s in raw_segments if s["source"] == key), key=lambda s: s["order"])
        if not mine:
            continue
        sub = {"schema_version": SCHEMA_VERSION, "content_mode": "multi_clip", "project": plan.get("project"),
               "source": {"path": meta.get("source_path"), "duration_ms": meta.get("duration_ms")},
               "allow_source_overlap": plan.get("allow_source_overlap") is True,
               "segments": [dict(s, order=i) for i, s in enumerate(mine, 1)]}
        try:
            validated = validate_plan_data(sub, meta, transcript)
        except CliError as exc:
            raise CliError(f"素材 {key} 的剪辑段校验失败：{exc}") from exc
        units = transcript_units(transcript, int(meta["duration_ms"]))
        for seg in validated.segments:
            _strict_checks(key, seg, units)
            cuts = seg.get("cut") or []
            if not isinstance(cuts, list) or not all(isinstance(c, list) and len(c) == 2 and all(isinstance(v, int) for v in c) for c in cuts):
                raise CliError(f"剪辑段 {seg['id']} 的 cut 要写成 [[开始毫秒, 结束毫秒], ...]，从审阅包 ✂ 标记后面抄")
            for c0, c1 in cuts:
                for u in units:
                    for w in u.get('words',[]):
                        if w['start_ms'] < c0 < w['end_ms'] or w['start_ms'] < c1 < w['end_ms']:
                            raise CliError(f"{seg['id']} 的 cut 切进「{w['text']}」内部；用真实词边界")
                    if not u.get('words') and min(c1,u['end_ms'])>max(c0,u['start_ms']) and not (c0<=u['start_ms'] and c1>=u['end_ms']):
                        raise CliError('只有句级时间，不能删除半句话；先补词级转写或整句取舍')
                if not seg["source_start_ms"] <= c0 < c1 <= seg["source_end_ms"]:
                    raise CliError(f"剪辑段 {seg['id']} 的 cut [{c0}, {c1}] 不在这段区间 {seg['source_start_ms']}–{seg['source_end_ms']} 里")
        vad_path = Path(str(resolve_batch_reference(plan_path, ref.get("source_meta"), ""))).parent / "vad.json"
        vad = load_json(vad_path, "声音地图") if vad_path.is_file() else {}
        loaded_extra = {"silences": vad.get("silences_ms", []) if vad.get("auto_trim_reliable",False) else [], "rms": vad.get("rms_db_10ms"), "unit_list": units}
        loaded[key] = {**loaded_extra, "meta": meta, "units": validated.units_by_id, "frame": frame_duration_fraction(meta),
                       "path": require_file(Path(meta["source_path"]), f"素材 {key}"),
                       "source_meta_path": str(resolve_batch_reference(plan_path, ref.get("source_meta"), "")),
                       "transcript_path": str(resolve_batch_reference(plan_path, ref.get("transcript"), ""))}
        for seg in validated.segments:
            if seg["id"] in validated_by_id:
                raise CliError(f"剪辑段 id 重复：{seg['id']}")
            validated_by_id[seg["id"]] = (key, seg)
    frames = {k: v["frame"] for k, v in loaded.items()}
    frame = 1/Fraction(str(plan["fps"])) if plan.get("fps") else next(iter(frames.values()))
    tighten = plan.get("tighten", True) is not False
    pauses = plan.get("pauses") if isinstance(plan.get("pauses"), dict) else None
    items: list[MultiItem] = []
    cursor = Fraction(0)
    stats = {"planned_ms": 0, "kept_ms": 0, "cuts": 0, "pauses_compressed": 0, "no_acoustic_map": sorted(k for k, v in loaded.items() if v["rms"] is None)}
    for seg in sorted(raw_segments, key=lambda s: s["order"]):
        key, vseg = validated_by_id[seg["id"]]
        vseg = dict(vseg, order=seg["order"])
        words = [(int(w["start_ms"]), int(w["end_ms"])) for uid in vseg["evidence_unit_ids"]
                 for w in (loaded[key]["units"][uid].get("words") or [])]
        pieces = tighten_ranges(vseg["source_start_ms"], vseg["source_end_ms"],
                                loaded[key]["silences"] if tighten else [], vseg.get("cut"), pauses,
                                keep_pauses=vseg.get("keep_pauses") is True or not tighten,
                                words=words, rms=loaded[key]["rms"])
        stats["planned_ms"] += vseg["source_end_ms"] - vseg["source_start_ms"]
        stats["cuts"] += len(vseg.get("cut") or [])
        stats["pauses_compressed"] += max(0, len(pieces) - 1 - len(vseg.get("cut") or []))
        prev_end = None
        for k, (a, b) in enumerate(pieces, 1):
            piece = dict(vseg, source_start_ms=a, source_end_ms=b, id=vseg["id"] if len(pieces) == 1 else f"{vseg['id']}.{k}")
            from cy_quality import frame_bounds
            others = [u for u in loaded[key]['unit_list'] if u['id'] not in vseg['evidence_unit_ids']]
            lower = max([0]+[u['end_ms']/1000 for u in others if u['end_ms']<=a])
            upper = min([loaded[key]['meta']['duration_ms']/1000]+[u['start_ms']/1000 for u in others if u['start_ms']>=b])
            blocked = [(u['start_ms']/1000,u['end_ms']/1000) for u in others]
            blocked += [(x/1000,y/1000) for x,y in (vseg.get('cut') or [])]
            # Exclude automatically removed internal gaps too.
            blocked += [(pieces[j-1][1]/1000,pieces[j][0]/1000) for j in range(1,len(pieces))]
            try:
                lo,hi=frame_bounds(a/1000,b/1000,[(x/1000,y/1000) for x,y in words],float(1/frame),lower,upper,blocked)
            except ValueError as exc:raise CliError(f"{key}/{vseg['id']}：{exc}") from exc
            start, end = lo*frame, hi*frame
            if prev_end is not None and start < prev_end:
                start = prev_end
            if end <= start:
                continue
            prev_end = end
            stats["kept_ms"] += b - a
            items.append(MultiItem(key, piece, loaded[key]["path"], loaded[key]["meta"], loaded[key]["units"],
                                   start, end, end - start, cursor))
            cursor += end - start
    plan = dict(plan, _stats=stats)
    return plan, items, frame, loaded


def _canvas(plan: dict[str, Any], loaded: dict[str, dict[str, Any]]) -> tuple[Any, Any]:
    canvas = plan.get("canvas") if isinstance(plan.get("canvas"), dict) else {}
    entry = next((v for _, v in sorted(loaded.items()) if isinstance(v["meta"].get("video"), dict)), None)
    first = entry["meta"]["video"] if entry else {}
    width, height = first.get("width"), first.get("height")
    # 手机竖拍常是编码 1920×1080 加 ±90° 旋转：画布按显示方向，否则竖片会被排成横版。
    if entry and abs(_rotation(Path(entry["path"]))) % 180 == 90:
        width, height = height, width
    return canvas.get("width") or width, canvas.get("height") or height


# Shared layout; its support status is documented separately from XML generation.
from cy_packaging import layout_for

def multi_fcpxml(plan: dict[str, Any], items: list[MultiItem], frame: Fraction, loaded: dict[str, dict[str, Any]],
                 captions: list[dict[str, Any]] | None = None, asset_dir: Path | None = None) -> tuple[str, list[str]]:
    width, height = _canvas(plan, loaded)
    resources = ET.Element("resources")
    seq_format = {"id": "r1", "frameDuration": fcpxml_time(frame)}
    if width and height:
        seq_format.update(width=str(width), height=str(height))
    ET.SubElement(resources, "format", seq_format)
    refs: dict[str, str] = {}
    for n, (key, info) in enumerate(sorted(loaded.items()), 1):
        meta, video, audio = info["meta"], info["meta"].get("video"), info["meta"].get("audio")
        fmt_id = f"f{n}"
        fmt = {"id": fmt_id, "frameDuration": fcpxml_time(info["frame"])}
        if isinstance(video, dict) and isinstance(video.get("width"), int):
            fmt.update(width=str(video["width"]), height=str(video["height"]))
        ET.SubElement(resources, "format", fmt)
        attrs = {"id": f"a{n}", "name": info["path"].name, "src": info["path"].as_uri(), "start": ms_time(int(meta.get("start_ms") or 0)),
                 "duration": fcpxml_time(ceil_to_frame(Fraction(int(meta["duration_ms"]), 1000), frame))}
        if isinstance(video, dict):
            attrs.update(hasVideo="1", format=fmt_id)
        if isinstance(audio, dict):
            attrs.update(hasAudio="1", audioSources=str(meta.get("audio_stream_count") or 1),
                         audioRate=str(audio.get("sample_rate") or 48000))
            channel_counts = {int(t.get("channels") or 2) for t in meta.get("audio_streams", [])}
            if len(channel_counts) <= 1:
                attrs["audioChannels"] = str(next(iter(channel_counts), int(audio.get("channels") or 2)))
        uri=attrs.pop("src");asset=ET.SubElement(resources,"asset",attrs);ET.SubElement(asset,"media-rep",kind="original-media",src=uri)
        refs[key] = f"a{n}"
    root = ET.Element("fcpxml", {"version": "1.10"})
    root.append(resources)
    event = ET.SubElement(ET.SubElement(root, "library"), "event", {"name": "口播剪辑"})
    project_node = ET.SubElement(event, "project", {"name": str(plan["project"]["name"]).strip()})
    total = sum((i.duration for i in items), Fraction(0))
    sequence = ET.SubElement(project_node, "sequence", {"format": "r1", "duration": fcpxml_time(total), "tcStart": "0s",
                                                        "tcFormat": "NDF", "audioLayout": "stereo", "audioRate": "48k"})
    spine = ET.SubElement(sequence, "spine")
    clips: list[tuple[Any, MultiItem]] = []
    for item in items:
        attrs = {"name": f"{item.segment['order']:03d}_{item.key}_{item.segment['id']}_{item.segment['role']}",
                 "ref": refs[item.key], "offset": fcpxml_time(item.offset), "start": fcpxml_time(item.source_start + Fraction(int(item.source_meta.get("start_ms") or 0),1000)),
                 "duration": fcpxml_time(item.duration)}
        if isinstance(item.source_meta.get("audio"), dict):
            attrs["audioRole"] = "dialogue"
        clips.append((ET.SubElement(spine, "asset-clip", attrs), item))
    notes: list[str] = []
    if asset_dir is not None and width and height:
        from cy_packaging import decorate
        hosts=[{'node':node,'id':it.segment['id'],'start':it.source_start+Fraction(int(it.source_meta.get('start_ms') or 0),1000),'offset':it.offset,'duration':it.duration,
                'unit_ids':it.segment['evidence_unit_ids']} for node,it in clips]
        cs=[{'start':c['start_ms']/1000,'end':c['end_ms']/1000,'text':c['text']} for c in (captions or [])]
        try: notes=decorate(root,hosts,cs,plan,asset_dir,int(width),int(height),frame)
        except ValueError as exc: raise CliError(str(exc)) from exc
    for node,it in clips:
        tracks=it.source_meta.get('audio_streams',[])
        if len(tracks)>1:
            target=it.source_meta['audio']['stream_index'];channel=1
            for track in tracks:
                count=int(track.get('channels') or 2)
                if track['index']==target:
                    ET.SubElement(node,'audio-channel-source',srcCh=', '.join(str(i) for i in range(channel,channel+count)),role='dialogue');break
                channel+=count
    try:
        ET.indent(root, space="  ")
    except AttributeError:  # pragma: no cover
        pass
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode") + "\n", notes


# ---------------------------------------------------------------- 试听版与复查
def build_preview_audio(items, out_path):
    import wave
    rate=16000;cache={};out_path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(out_path),'wb') as dest:
        dest.setparams((1,2,rate,0,'NONE','not compressed'));written=0
        for it in items:
            key=str(it.source_path)
            if key not in cache:
                path=out_path.parent/('analysis-'+hashlib.sha256(key.encode()).hexdigest()[:12]+'.wav')
                meta=it.source_meta;audio=meta.get('audio') or {};delta=(int(audio.get('start_ms') or 0)-int(meta.get('start_ms') or 0))/1000
                subprocess.run(['ffmpeg','-nostdin','-y','-v','error','-i',key,'-map',f"0:{audio.get('stream_index',0)}",'-vn',
                                '-af',f'asetpts=PTS-STARTPTS+({delta})/TB,aresample=16000:async=1:first_pts=0,apad',
                                '-t',str(meta['duration_ms']/1000),'-ar',str(rate),'-ac','1','-c:a','pcm_s16le',str(path)],check=True)
                cache[key]=path
            target=round(float(it.offset+it.duration)*rate)-written
            with wave.open(str(cache[key]),'rb') as src:
                src.setpos(min(src.getnframes(),round(float(it.source_start)*rate)))
                while target:
                    n=min(target,rate*30);raw=src.readframes(n)
                    if len(raw)!=n*2:raise CliError('试听源音频不足，不能静默判为同步通过')
                    dest.writeframes(raw);target-=n;written+=n
    return out_path

def _where(items: list[MultiItem], t: float) -> str:
    it = next((i for i in items if float(i.offset) * 1000 <= t < float(i.offset + i.duration) * 1000), items[-1])
    src = float(it.source_start) * 1000 + t - float(it.offset) * 1000
    return f"成片 {format_clock(int(t))}（素材 {it.key} {format_clock(int(src))}，段 {it.segment['id'].split('.')[0]}）"


def recheck_audio(items: list[MultiItem], preview: Path) -> dict[str, list[str]]:
    """对试听版重新量声音：还剩的长停顿、孤立的短促发声（需回听，可能包含正常词）。"""
    amap = acoustic_map(preview)
    total = sum(float(i.duration) for i in items) * 1000
    sil = [(x, y) for x, y in amap["silences_ms"] if y - x >= 1000 and x > 300 and y < total - 300]
    speech, cur = [], 0
    for x, y in amap["silences_ms"]:
        if x > cur:
            speech.append((cur, x))
        cur = max(cur, y)
    bursts = [(a, b) for k, (a, b) in enumerate(speech)
              if 80 <= b - a <= 450 and (k == 0 or a - speech[k - 1][1] >= 300) and (k + 1 == len(speech) or speech[k + 1][0] - b >= 300)]
    return {"long_pauses": [f"{_where(items, x)} 停顿 {(y - x) / 1000:.1f} 秒" for x, y in sil],
            "short_bursts": [f"{_where(items, a)} 短促发声 {(b - a) / 1000:.2f} 秒（需回听，不能自动认作咳嗽）" for a, b in bursts]}


def recheck_asr(items: list[MultiItem], preview: Path, captions: list[dict[str, Any]], backend: str | None, model: str | None) -> dict[str, list[str]] | None:
    """把试听版重新转写一遍，和计划保留的原话逐字比对：多出来的声音（上一句的尾巴、没删掉的话）、
    可能被吞掉的字、残留的语气词和口癖。缺依赖时自动准备，失败时报告未完成。"""
    import difflib
    import importlib.util

    backend = pick_backend(backend)
    ensure_python_module("mlx_whisper" if backend == "mlx" else "faster_whisper", "mlx-whisper==0.4.3" if backend == "mlx" else "faster-whisper==1.2.1")
    model = default_model(backend, model)
    wav = preview
    # 复查语言跟着保留字幕走：中文为主用中文提示词，其他语言交给 Whisper 自动识别，不能写死中文。
    text = "".join(str(c.get("text", "")) for c in captions)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    lang = "zh" if text and cjk / max(1, len(text.replace(" ", ""))) > 0.3 else None
    prompt = "嗯，那个，我我我们今天就是，就是说一下这个，呃，这个事情。以下是普通话口语，照实记录语气词和重复，使用简体中文。" if lang == "zh" else None
    _prepare_model_download()
    from cy_quality import retranscribe
    if backend=='mlx':
        import mlx_whisper
        call=lambda path:mlx_whisper.transcribe(str(path),path_or_hf_repo=model,language=lang,word_timestamps=True,initial_prompt=prompt,condition_on_previous_text=False)
    else:
        engine=_FasterWhisperAdapter(model,None)
        call=lambda path:engine.transcribe(str(path),lang,prompt)
    result=retranscribe(wav,preview.parent/'re-asr',call,key=f'{backend}:{model}:{lang}')
    atomic_write_json(preview.parent/'成片再转写.json',result)
    from cy_quality import compare_asr
    return compare_asr(captions,result,lambda t:{'location':_where(items,t*1000)})


def final_script(items: list[MultiItem], captions: list[dict[str, Any]]) -> list[str]:
    """成片文稿：按成片顺序，每个剪辑段一行原话，用来通读检查重复和逻辑。"""
    by_seg: dict[str, list[str]] = {}
    order: list[tuple[str, float]] = []
    for c in captions:
        base = c["edit_segment_id"].split(".")[0]
        if base not in by_seg:
            by_seg[base] = []
            order.append((base, c["start_ms"]))
        by_seg[base].append(c["text"])
    return [f"[{format_clock(int(t))}] {sid}：{' '.join(by_seg[sid])}" for sid, t in order]


# ---------------------------------------------------------------- 字幕
PUNCT_CHARS = "，。！？、；：,.!?;:…“”\"'（）()《》"
PUNCT_RE = re.compile("[" + re.escape(PUNCT_CHARS) + "]")


def clean_caption(text: str, strip_punct: bool, corrections: dict[str, str]) -> str:
    """去换行；去句读但保护数字里的符号（3.5、1,000、3:1、10：30）；最后套用字幕校正表。"""
    text = re.sub(r"\s*\n\s*", " ", text)
    if strip_punct:
        out = []
        for i, ch in enumerate(text):
            if ch in PUNCT_CHARS:
                between_digits = ch in ".,:：" and 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit()
                out.append(ch if between_digits else " ")
            else:
                out.append(ch)
        text = "".join(out)
    text = re.sub(r"\s{2,}", " ", text).strip()
    for wrong, right in corrections.items():  # 校正表最后套用，用户写的标点原样保留
        text = text.replace(wrong, right)
    return text


def _display_width(text: str) -> float:
    from cy_packaging import text_px
    return text_px(unicodedata.normalize("NFKC", text).strip(), 1.0)


def _split_caption(unit: dict[str, Any], a: int, b: int, max_chars: int) -> list[tuple[int, int, str]]:
    """一条转写单元 → 若干单行。有字级时间时按字切，每行不超过 max_chars 个字，尽量断在标点处；
    只有句级时间（SRT 导入）时不拆，整句一条。"""
    words = [w for w in unit.get("words") or [] if int(w["end_ms"]) > a and int(w["start_ms"]) < b]
    if not words or max_chars <= 0:
        return [(a, b, words_for_intersection(unit, a, b))]
    # 均分每行长度：按总字数算出要几行，再按平均字数切，避免最后一行只剩一两个字（如整句 14 字时切出「了」）
    # 按显示宽度计：汉字 1，英文字母约 0.55，空格 0.3——英文口播不会一行只剩一两个词
    lengths = [_display_width(str(w["text"])) for w in words]
    total_chars = sum(lengths)
    limit = max_chars
    if total_chars > max_chars:
        limit = total_chars / math.ceil(total_chars / max_chars - 1e-9)
    lines, cur, count = [], [], 0
    for w, n in zip(words, lengths):
        t = str(w["text"])
        if cur and count + n > limit:
            lines.append(cur)
            cur, count = [], 0
        cur.append(w)
        count += n
        if PUNCT_RE.search(t[-1:]) and count >= limit * 0.5:
            lines.append(cur)
            cur, count = [], 0
    if cur:
        lines.append(cur)
    out = []
    for i, ln in enumerate(lines):
        s0 = max(a, int(ln[0]["start_ms"])) if i else a
        s1 = min(b, int(lines[i + 1][0]["start_ms"])) if i + 1 < len(lines) else b
        if s1 > s0:
            out.append((s0, s1, "".join(str(w["text"]) for w in ln)))
    return out


def multi_captions(items: list[MultiItem], max_chars: int, strip_punct: bool, corrections: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    captions, notes = [], []
    for item in items:
        seg = item.segment
        units = sorted((item.units_by_id[u] for u in seg["evidence_unit_ids"]), key=lambda u: (u["start_ms"], u["end_ms"]))
        for unit in units:
            a = max(round_fraction_to_ms(item.source_start), unit["start_ms"])
            b = min(round_fraction_to_ms(item.source_end), unit["end_ms"])
            if b <= a:
                continue
            for c0, c1, raw in _split_caption(unit, a, b, max_chars):
                text = clean_caption(raw, strip_punct, corrections)
                if text:
                    captions.append({"start_ms": round_fraction_to_ms(item.offset + Fraction(c0, 1000) - item.source_start),
                                     "end_ms": round_fraction_to_ms(item.offset + Fraction(c1, 1000) - item.source_start),
                                     "text": text, "source": item.key, "source_unit_id": unit["id"], "edit_segment_id": seg["id"]})
    captions.sort(key=lambda c: (c["start_ms"], c["end_ms"]))
    for prev, nxt in zip(captions, captions[1:]):
        if prev["end_ms"] > nxt["start_ms"]:
            prev["end_ms"] = nxt["start_ms"]
    captions = [c for c in captions if c["end_ms"] > c["start_ms"]]
    short = [c for c in captions if c["end_ms"] - c["start_ms"] < 400]
    if short:
        notes.append(f"{len(short)} 条字幕显示不到 0.4 秒（如「{short[0]['text']}」），导入后看一眼是否需要合并")
    if max_chars > 0:
        long_lines = [c for c in captions if _display_width(c["text"]) > max_chars + 0.5]
        if long_lines:
            notes.append(f"{len(long_lines)} 条字幕超过每行 {max_chars} 字（来自只有句级时间的 SRT，无法按字拆），在剪辑软件里手动断行")
    return captions, notes


def _load_corrections(path: Path | None) -> dict[str, str]:
    if not path or not path.is_file():
        return {}
    data = load_json(path, "字幕校正表")
    table = data.get("替换", data)
    if not isinstance(table, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in table.items()):
        raise CliError("字幕校正表格式：{\"替换\": {\"错字\": \"正字\"}}")
    return table


def command_validate_multi(args: argparse.Namespace) -> int:
    plan, items, frame, loaded = validate_multi(Path(args.plan))
    total = sum((i.duration for i in items), Fraction(0))
    pending = sorted({f"{i.key}:{i.segment['id'].split('.')[0]}" for i in items if i.segment.get("needs_confirmation") is True})
    print(json.dumps({"ok": True, "sources": sorted(loaded), "segment_count": len(items),
                      "output_duration": format_clock(round_fraction_to_ms(total)), "pending_confirmation": pending},
                     ensure_ascii=False, indent=2))
    return 0


def command_compile_multi(args: argparse.Namespace) -> int:
    """剪辑计划 → 交付目录里只有一个 FCPXML（字幕、内容标题、封面标题参考、进度条都在里面）。
    字幕、试听与复查放在独立的编译记录目录，保留旧版本和工程依赖。"""
    plan_path = Path(args.plan).expanduser().resolve()
    plan, items, frame, loaded = validate_multi(plan_path)
    stats = plan.pop("_stats", {})
    name = sanitize_filename(plan["project"]["name"])
    out_dir = Path(args.out).expanduser().resolve()
    if out_dir.suffix.lower() == ".fcpxml":
        out_dir, name = out_dir.parent, out_dir.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    fcpxml_path = out_dir / f"{name}.fcpxml"
    if any(f.name != fcpxml_path.name for f in out_dir.iterdir()):
        raise CliError('交付目录已有其他文件，请用新的空目录；不会自动删掉用户文件')
    if fcpxml_path.exists() and not args.force:
        raise CliError(f"{fcpxml_path.name} 已存在，不覆盖旧版本；换新的交付目录（交付_v2）或加 --force")
    workdir = plan_path.parent
    if (workdir/'asr-options.json').exists():
        opts=load_json(workdir/'asr-options.json','识别配置')
        if args.backend=='auto':args.backend=opts.get('backend','auto')
        if args.model is None:args.model=opts.get('model')
    from lve_feedback_flow import experience
    import uuid
    ensure_feedback_project(workdir)
    experience_result = experience(workdir, shared_experience_data(), "单主题口播清理")
    records = workdir / "编译记录" / (name + "-" + uuid.uuid4().hex[:8])
    records.mkdir(parents=True, exist_ok=False)
    atomic_write_json(records / "本次经验采用快照.json", experience_result)
    width, height = _canvas(plan, loaded)
    lay = layout_for(width, height)
    max_chars = args.max_chars if args.max_chars is not None else lay["sub_chars"]
    corr_path = Path(args.corrections).expanduser().resolve() if args.corrections else workdir / "字幕校正.json"
    corrections = _load_corrections(corr_path)
    captions, caption_notes = multi_captions(items, max_chars, not args.keep_punct, corrections)
    xml_text, pack_notes = multi_fcpxml(plan, items, frame, loaded, captions, records / "包装素材")
    doc = ET.fromstring(xml_text.split("\n", 2)[2])
    cursor = Fraction(0)
    for clip in doc.find("./library/event/project/sequence/spine").findall("asset-clip"):
        if parse_fcpxml_time(clip.get("offset")) != cursor:
            raise CliError("内部错误：主轨不连续")
        cursor += parse_fcpxml_time(clip.get("duration"))
    atomic_write_text(fcpxml_path, xml_text)

    pending, _seen = [], set()
    for i in items:
        base = i.segment["id"].split(".")[0]
        if i.segment.get("needs_confirmation") is True and base not in _seen:
            _seen.add(base)
            pending.append(i)
    atomic_write_text(records / f"{name}.srt", srt_document(captions))
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["order", "source", "id", "source_start", "source_end", "target_start", "target_end", "reason", "transcript"])
    for i in items:
        writer.writerow([i.segment["order"], i.key, i.segment["id"], format_clock(round_fraction_to_ms(i.source_start)),
                         format_clock(round_fraction_to_ms(i.source_end)), format_clock(round_fraction_to_ms(i.offset)),
                         format_clock(round_fraction_to_ms(i.offset + i.duration)), i.segment["reason"], i.segment["transcript"]])
    atomic_write_text(records / f"{name}_剪辑明细.csv", out.getvalue())
    atomic_write_json(records / f"{name}_计划副本.json",
                      dict(plan, sources={k: {"source_meta": v["source_meta_path"], "transcript": v["transcript_path"]} for k, v in loaded.items()}))

    audio_check = asr_check = None
    if not args.no_recheck:
        try:
            preview = build_preview_audio(items, records / f"{name}_试听版.wav")
            audio_check = recheck_audio(items, preview)
            asr_check = recheck_asr(items, preview, captions, args.backend, args.model)
        except Exception as exc:  # noqa: BLE001  复查失败不挡交付，写进报告
            asr_check = {"error": [f"重新转写复查失败：{exc}"]}
    total = sum((i.duration for i in items), Fraction(0))
    notes = pack_notes + caption_notes
    if pending:
        notes.extend(f"待确认 {i.key}:{i.segment['id']}：{i.segment.get('confirm_note') or i.segment['reason']}" for i in pending)
    from cy_quality import write_review
    rows=[{'key':c['source']+':'+c['source_unit_id'],'start':c['start_ms']/1000,'end':c['end_ms']/1000,'text':c['text']} for c in captions]
    common_review=write_review(records,rows,{'asr':asr_check or {'status':'not_run'},'audio':audio_check or {'status':'not_run'},'layout':notes})
    counts = {"duplicates": common_review['pairs']}
    if audio_check:
        counts.update({k: len(v) for k, v in audio_check.items()})
    if asr_check:
        counts.update({k:len(v) for k,v in asr_check.items() if isinstance(v,list)})
    print(json.dumps({"ok": True, "status": "awaiting_content_review", "fcpxml": str(fcpxml_path),
                      "review": common_review["review"], "records": str(records), "experience": experience_result, "output_duration": format_clock(round_fraction_to_ms(total)),
                      "pending_confirmation": [f"{i.key}:{i.segment['id'].split('.')[0]}" for i in pending],
                      "recheck_counts": counts, "packaging_notes": notes,
                      "next": "通读成片复查.md；修订后重编译；用 review 命令登记本轮结论，未完成听审不能称为精剪验收通过"}, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- 反馈沉淀
FEEDBACK_SCOPES = {"项目": "只针对这条片子", "个人": "这个用户以后都这样", "通用": "换一个用户也成立"}


from lve_common import shared_experience_data

def ensure_feedback_project(project):
    import uuid
    project=Path(project).expanduser().resolve();project.mkdir(parents=True,exist_ok=True)
    manifest=project/'project.json'
    if not manifest.exists():atomic_write_json(manifest,{'schema_version':3,'id':'sv-'+uuid.uuid4().hex[:20],'name':project.parent.name if project.name in ('工作','work') else project.name,'skill':'cy-edit-shortvideo'})
    return project

def command_feedback(args):
    from lve_feedback_flow import feedback
    project=ensure_feedback_project(args.project)
    if args.kb:os.environ["CY_EDIT_EXPERIENCE_DIR"]=str(Path(args.kb).expanduser().resolve())
    if args.input:path=Path(args.input)
    else:
        if not args.text:raise CliError('需要 --text 用户原话或 --input 本轮反馈 JSON')
        scope={'项目':'project','个人':'user','通用':'candidate'}.get(args.scope)
        if not scope:raise CliError('范围使用 项目/个人/通用')
        item={'text':args.text,'scope':scope,'lesson':args.rule or args.text,'action':args.change or '已记录，等待修订',
              'category':'correction','applies_when':args.applies_when or '符合本条反馈描述的剪辑任务',
              'preference_key':args.topic}
        if args.authorization:item['authorization']=args.authorization
        if args.revoke or args.replaces:
            item.update(operation='revoke' if args.revoke else 'update',target_ids=[args.revoke or args.replaces])
        raw={'message':args.text,'items':[item]}
        path=project/'feedback/inputs'/('input-'+hashlib.sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]+'.json')
        atomic_write_json(path,raw)
    result=feedback(project,path,Path(args.kb) if args.kb else shared_experience_data())
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0

def command_experience(args):
    from lve_feedback_flow import experience
    project=ensure_feedback_project(args.project)
    if args.kb:os.environ["CY_EDIT_EXPERIENCE_DIR"]=str(Path(args.kb).expanduser().resolve())
    result=experience(project,Path(args.kb) if args.kb else shared_experience_data(),None,args.decisions_file)
    # Legacy MD is retained as evidence, never silently promoted into an active rule.
    legacy=user_data_dir()/'经验库'
    result['legacy_reference_files']=[str(p) for p in legacy.glob('*.md')] if legacy.exists() else []
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0

def command_review(args):
    from cy_quality import record_review
    try: result=record_review(args.records,args.input)
    except ValueError as exc:raise CliError(str(exc)) from exc
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0

def add_extension_parsers(subparsers: Any) -> None:
    transcribe = subparsers.choices["transcribe"]
    transcribe.description = "跨平台转写：Apple 芯片 Mac 用 mlx-whisper，其他用 faster-whisper；缺库、缺模型时自动下载"
    for action in list(transcribe._actions):
        if action.dest in ("model", "model_cache"):
            action.required = False
            action.default = None
    transcribe.add_argument("--backend", default="auto", choices=["auto", "mlx", "faster-whisper"], help="默认 auto")
    transcribe.add_argument("--initial-prompt", default=None, help="专有名词，逗号分隔，提高识别准确率")
    transcribe.set_defaults(handler=command_transcribe_any)

    prepare = subparsers.add_parser("prepare", help="整理整个素材文件夹：编号、读参数、抽音频、转写（或导入同名 SRT）、切审阅包")
    prepare.add_argument("--sources", required=True, help="放视频的文件夹（一条或多条都行）")
    prepare.add_argument("--workdir", required=True, help="工作目录，放转写和审阅包")
    prepare.add_argument("--backend", default="auto", choices=["auto", "mlx", "faster-whisper"])
    prepare.add_argument("--model", default=None, help="默认 large-v3-turbo；电脑慢可用 small（两个后端都认这两个名字）")
    prepare.add_argument("--model-cache", default=None, help="模型缓存目录；默认系统缓存")
    prepare.add_argument("--language", default="zh")
    prepare.add_argument("--hotwords", default=None, help="专有名词，逗号分隔；不写时读 素材文件夹上一级 需求.md 里的「专有名词：」")
    prepare.add_argument("--conform-fps", default=None, help="帧率不一致时经用户同意再用：为少数派生成该帧率的派生副本，原片不动")
    prepare.add_argument("--chunk-minutes", type=float, default=DEFAULT_CHUNK_MINUTES)
    prepare.add_argument("--packet-minutes", type=float, default=DEFAULT_PACKET_MINUTES)
    prepare.set_defaults(handler=command_prepare)

    vm = subparsers.add_parser("validate-multi", help="校验剪辑计划")
    vm.add_argument("--plan", required=True)
    vm.set_defaults(handler=command_validate_multi)

    cm = subparsers.add_parser("compile-multi", help="剪辑计划 → 交付目录只有一个 FCPXML（字幕和包装都在里面）+ 内部复查报告")
    cm.add_argument("--plan", required=True)
    cm.add_argument("--out", required=True, help="交付目录（如 交付_v1），里面只会有一个 .fcpxml")
    cm.add_argument("--force", action="store_true", help="覆盖已存在的交付文件（默认拒绝）")
    cm.add_argument("--max-chars", type=int, default=None, help="字幕每行最多几个字；默认竖屏 13、横屏 24；0 表示不拆")
    cm.add_argument("--keep-punct", action="store_true", help="字幕保留标点（默认去句读，数字里的 . , : 保留）")
    cm.add_argument("--corrections", default=None, help="字幕校正表；默认用计划旁边的 字幕校正.json")
    cm.add_argument("--no-recheck", action="store_true", help="不生成试听版、不复查（仅用户明确要求跳过时用，保持待审）")
    cm.add_argument("--backend", default="auto", choices=["auto", "mlx", "faster-whisper"], help="复查重新转写用的后端")
    cm.add_argument("--model", default=None, help="复查重新转写用的模型，默认同 prepare")
    cm.set_defaults(handler=command_compile_multi)

    rv = subparsers.add_parser('review', help='登记对最新成片文稿和声音的实际复查')
    rv.add_argument('--records',required=True);rv.add_argument('--input',required=True)
    rv.set_defaults(handler=command_review)

    fb = subparsers.add_parser("feedback", help="把用户反馈原话沉淀成 MD")
    fb.add_argument('--input');fb.add_argument('--authorization');fb.add_argument('--applies-when')
    fb.add_argument("--project", required=True, help="项目文件夹")
    fb.add_argument("--text", help="用户原话，原样照抄；撤销时写撤销原因")
    fb.add_argument("--scope", default="项目", help="项目（默认）/ 个人（用户说了以后都这样）/ 通用")
    fb.add_argument("--topic", default="其他", help="精剪、停顿、顺序、开头、素材、字幕、工程、交付、其他")
    fb.add_argument("--rule", help="沉淀成的一句规则（写判断依据和不适用的情况）")
    fb.add_argument("--change", help="这次据此改了什么")
    fb.add_argument("--replaces", help="新规则替代的旧规则编号（如 F1a2b3c4），旧规则停止生效")
    fb.add_argument("--revoke", help="撤销的规则编号，只撤销不新增")
    fb.add_argument("--kb", help="经验库目录；默认 用户数据目录/经验库")
    fb.set_defaults(handler=command_feedback)

    ex = subparsers.add_parser("experience", help="开工前读经验：打印仍生效的个人偏好和经验")
    ex.add_argument('--project',required=True);ex.add_argument('--decisions-file')
    ex.add_argument("--kb", help="经验库目录；默认同 feedback")
    ex.set_defaults(handler=command_experience)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="editor.py",
        description="cy-edit-shortvideo 剪辑引擎。新项目只用 prepare → validate-multi → compile-multi → feedback / experience；其余为底层命令。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="检查 ffmpeg/ffprobe、Python 和工作盘空间")
    doctor.add_argument("--workdir", help="检查该工作目录所在磁盘；默认当前目录")
    doctor.set_defaults(handler=command_doctor)

    probe = subparsers.add_parser("probe", help="读取源视频元数据")
    probe.add_argument("--source", required=True, help="源视频绝对或相对路径")
    probe.add_argument("--out", required=True, help="输出 source_meta.json")
    probe.set_defaults(handler=command_probe)

    extract_audio = subparsers.add_parser("extract-audio", help="不复制原片，抽取 16kHz 单声道 FLAC 音频分片")
    extract_audio.add_argument("--source", required=True, help="源视频路径")
    extract_audio.add_argument("--workdir", required=True, help="应位于有足够空间的源盘或外置盘")
    extract_audio.add_argument("--chunk-minutes", type=float, default=DEFAULT_CHUNK_MINUTES, help="每段分钟数，默认 20")
    extract_audio.add_argument("--manifest", help="manifest 路径；默认 WORKDIR/audio_manifest.json")
    extract_audio.set_defaults(handler=command_extract_audio)

    transcribe = subparsers.add_parser("transcribe", help="逐块转写（Mac 芯片用 mlx-whisper，其他用 faster-whisper；缺什么自动装），可断点续跑")
    transcribe.add_argument("--manifest", required=True, help="extract-audio 生成的 audio_manifest.json")
    transcribe.add_argument("--out", required=True, help="输出规范化 transcript.json")
    transcribe.add_argument(
        "--model",
        default="mlx-community/whisper-large-v3-turbo",
        help="MLX Whisper 模型，默认 mlx-community/whisper-large-v3-turbo",
    )
    transcribe.add_argument("--model-cache", required=True, help="模型缓存目录，应放在空间充足的源盘或外置盘")
    transcribe.add_argument("--language", default="zh", help="语言代码，默认 zh；自动识别传 auto")
    transcribe.set_defaults(handler=command_transcribe)

    import_srt = subparsers.add_parser("import-srt", help="将已有 SRT 规范化为带证据 ID 的 transcript.json")
    import_srt.add_argument("--srt", required=True, help="输入 SRT")
    import_srt.add_argument("--source", required=True, help="对应源视频")
    import_srt.add_argument("--out", required=True, help="输出 transcript.json")
    import_srt.add_argument("--language", default="zh", help="语言标签，默认 zh")
    import_srt.set_defaults(handler=command_import_srt)

    packets = subparsers.add_parser("packets", help="将长转写切成供 GPT 审阅的时间包")
    packets.add_argument("--transcript", required=True, help="规范化 transcript.json")
    packets.add_argument("--out-dir", required=True, help="新建的包输出目录")
    packets.add_argument("--packet-minutes", type=float, default=DEFAULT_PACKET_MINUTES, help="每包约多少分钟，默认 8")
    packets.set_defaults(handler=command_packets)

    validate = subparsers.add_parser("validate-plan", help="严格验证粗剪计划的边界、顺序、重叠与转写证据")
    validate.add_argument("--plan", required=True, help="edit_plan.json")
    validate.add_argument("--source-meta", required=True, help="probe 生成的 source_meta.json")
    validate.add_argument("--transcript", required=True, help="规范化 transcript.json")
    validate.add_argument("--out", help="可选 validation.json")
    validate.set_defaults(handler=command_validate_plan)

    validate_batch = subparsers.add_parser(
        "validate-batch", help="验证同一长素材拆出的 1–3 个独立 edit_plan"
    )
    validate_batch.add_argument("--batch", required=True, help="batch_plan.json")
    validate_batch.add_argument("--out", help="可选 batch_validation.json")
    validate_batch.set_defaults(handler=command_validate_batch)

    compile_parser = subparsers.add_parser("compile-fcpxml", help="编译 FCPXML、SRT、CSV 和粗剪报告")
    compile_parser.add_argument("--plan", required=True, help="已审核的 edit_plan.json")
    compile_parser.add_argument("--source-meta", required=True, help="probe 生成的 source_meta.json")
    compile_parser.add_argument("--transcript", required=True, help="规范化 transcript.json")
    compile_parser.add_argument("--out", required=True, help="输出 .fcpxml 路径或交付目录")
    compile_parser.add_argument("--force", action="store_true", help="覆盖同名交付文件；默认拒绝覆盖")
    compile_parser.set_defaults(handler=command_compile_fcpxml)

    compile_batch = subparsers.add_parser(
        "compile-batch", help="事务式编译 1–3 个独立 FCPXML 工程和批次清单"
    )
    compile_batch.add_argument("--batch", required=True, help="已验证的 batch_plan.json")
    compile_batch.add_argument("--out", required=True, help="必须尚不存在的批量交付目录")
    compile_batch.set_defaults(handler=command_compile_batch)

    verify = subparsers.add_parser("verify-fcpxml", help="校验 FCPXML 的原片 URI、剪辑段和目标时间线")
    verify.add_argument("--fcpxml", required=True, help="待校验 FCPXML")
    verify.add_argument("--source", required=True, help="应被引用的原片")
    verify.add_argument("--plan", required=True, help="对应 edit_plan.json")
    verify.add_argument("--dtd", help="可选 FCPXML 1.8 DTD；提供后用 xmllint 做严格 DTD 校验")
    verify.add_argument("--out", help="可选 verification.json")
    verify.set_defaults(handler=command_verify_fcpxml)
    add_extension_parsers(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows 控制台默认不是 UTF-8
        except Exception:  # noqa: BLE001
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command not in {"doctor", "feedback", "experience", "validate-plan", "validate-batch", "validate-multi", "review"}:
            ensure_ffmpeg()
        return int(args.handler(args))
    except Exception as exc:
        tail = "；保存失败时不能声称意见已全部保存" if args.command == "feedback" else ""
        print(f"错误：{exc}{tail}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
