#!/usr/bin/env python3

import argparse
import http.client
import json
import os
import re
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_MEDIA_PATH = Path("/workspace/mp3/test.mp3")
DEFAULT_CONFIG_PATH = Path(os.environ.get("TINGWU_CONFIG", "~/.config/tingwu-http/config.json")).expanduser()
DEFAULT_TASK_ROOT = Path(os.environ.get("TINGWU_TEMP_DIR", Path(__file__).resolve().parent / "temp"))
DEFAULT_POLL_INTERVAL = float(os.environ.get("TINGWU_POLL_INTERVAL", "10"))
DEFAULT_TIMEOUT = float(os.environ.get("TINGWU_TIMEOUT", "14400"))
SRT_MAX_CHARS = int(os.environ.get("TINGWU_SRT_MAX_CHARS", "36"))
SRT_MAX_DURATION_MS = int(os.environ.get("TINGWU_SRT_MAX_DURATION_MS", "8000"))
SRT_GAP_MS = int(os.environ.get("TINGWU_SRT_GAP_MS", "1500"))

ProgressCallback = Callable[[dict[str, Any]], None]


class TingwuCancelledError(RuntimeError):
    pass


def emit_event(callback, stage, **data):
    if callback:
        callback({"stage": stage, "timestamp": utc_now(), **data})


def ensure_not_cancelled(cancel_check):
    if cancel_check and cancel_check():
        raise TingwuCancelledError("听悟转写任务已取消")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def random_hex(byte_count):
    return secrets.token_hex(byte_count)


def write_private_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write(content)
    os.replace(temporary_path, path)
    os.chmod(path, 0o600)


def write_private_json(path, value):
    write_private_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def import_auth_config(request_snapshot_path, config_path):
    with open(request_snapshot_path, encoding="utf-8") as source:
        snapshot = json.load(source)
    headers = snapshot.get("requestHeaders") or {}
    cookie = headers.get("cookie")
    request_url = snapshot.get("url")
    if not cookie or not request_url:
        raise RuntimeError("请求快照缺少 cookie 或 url")
    parsed_url = urlsplit(request_url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    config = {
        "base_url": base_url,
        "cookie": cookie,
        "user_agent": headers.get("user-agent", "Mozilla/5.0"),
        "origin": headers.get("origin", base_url),
        "referer": headers.get("referer", f"{base_url}/folders/0"),
    }
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(config_path.parent, 0o700)
    write_private_json(config_path, config)
    print(f"[auth] 配置已写入 {config_path}")


def load_config(config_path):
    config_path = Path(config_path).expanduser().resolve()
    file_mode = config_path.stat().st_mode & 0o777
    if file_mode & 0o077:
        raise RuntimeError(f"认证配置权限必须为 600，当前为 {file_mode:o}: {config_path}")
    with open(config_path, encoding="utf-8") as source:
        config = json.load(source)
    required_keys = ["base_url", "cookie", "user_agent", "origin", "referer"]
    missing_keys = [key for key in required_keys if not config.get(key)]
    if missing_keys:
        raise RuntimeError(f"认证配置缺少字段: {', '.join(missing_keys)}")
    return config


def normalize_session_value(value):
    session = str(value or "").strip().replace("\r", "").replace("\n", "")
    if session.lower().startswith("cookie:"):
        session = session.split(":", 1)[1].strip()
    if not session or "=" not in session:
        raise ValueError("请粘贴浏览器请求中的完整听悟 Cookie/session 字符串")
    return session


def validate_auth_config(config_path):
    try:
        config = load_config(config_path)
        auth_info = check_auth(config, quiet=True)
        return {
            "configured": True,
            "valid": True,
            "message": "听悟 Session 有效。",
            **auth_info,
        }
    except Exception as error:
        return {
            "configured": Path(config_path).expanduser().is_file(),
            "valid": False,
            "message": f"听悟 Session 验证失败：{error}",
        }


def update_auth_session(config_path, session):
    config_path = Path(config_path).expanduser().resolve()
    try:
        current_config = load_config(config_path)
    except Exception as error:
        return {
            "configured": config_path.is_file(),
            "valid": False,
            "updated": False,
            "message": f"听悟 Session 更新失败：{error}",
        }
    updated_config = dict(current_config)
    updated_config["cookie"] = normalize_session_value(session)
    write_private_json(config_path, updated_config)
    status = validate_auth_config(config_path)
    if not status["valid"]:
        write_private_json(config_path, current_config)
        status["updated"] = False
        status["message"] = f"{status['message']}；已保留原有 Session，请粘贴新的完整 Cookie。"
        return status

    status["updated"] = True
    return status


def append_client_query(path):
    return f"{path}{'&' if '?' in path else '?'}c=web"


def tingwu_request(config, path, payload, timeout=60):
    base_url = config["base_url"].rstrip("/")
    request_url = urljoin(base_url + "/", append_client_query(path).lstrip("/"))
    request = Request(
        request_url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Cookie": config["cookie"],
            "Origin": config["origin"],
            "Referer": config["referer"],
            "User-Agent": config["user_agent"],
            "X-B3-Sampled": "1",
            "X-B3-SpanId": random_hex(8),
            "X-B3-TraceId": random_hex(16),
            "x-tw-canary": "",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status_code = response.status
            response_body = response.read()
    except HTTPError as error:
        status_code = error.code
        response_body = error.read()
    except URLError as error:
        raise RuntimeError(f"HTTP 请求失败: {error.reason}") from error
    try:
        result = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"接口返回非 JSON 内容，HTTP {status_code}") from error
    if status_code < 200 or status_code >= 300 or str(result.get("code")) != "0" or result.get("success") is False:
        code = result.get("code")
        message = result.get("message")
        if code == "CMN.NotLogin":
            message = "听悟 Session 已失效，请在转录设置中更新 Session"
        raise RuntimeError(f"接口请求失败: HTTP {status_code}, code={code}, message={message}")
    return result


def media_content_type(extension):
    content_types = {
        ".aac": "audio/aac",
        ".aiff": "audio/aiff",
        ".amr": "audio/amr",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".wma": "audio/x-ms-wma",
    }
    return content_types.get(extension, "application/octet-stream")


def create_upload_task(config, media_path):
    file_size = media_path.stat().st_size
    extension = media_path.suffix.lower()
    title = media_path.stem[:150]
    task_id = f"http-{int(time.time() * 1000)}-{uuid.uuid4()}"
    response = tingwu_request(
        config,
        "/api/trans/request?generatePutLink",
        {
            "action": "generatePutLink",
            "version": "1.0",
            "taskId": task_id,
            "useSts": 0,
            "fileSize": file_size,
            "dirId": 0,
            "fileContentType": media_content_type(extension),
            "tag": {
                "showName": title,
                "fileFormat": extension.lstrip("."),
                "fileType": "local",
                "lang": "cn",
                "roleSplitNum": -1,
                "translateSwitch": 0,
                "transTargetValue": 0,
                "originalTag": json.dumps({"isVideo": 0}, separators=(",", ":")),
                "client": "web",
            },
        },
    )
    upload = response.get("data") or {}
    required_keys = ["putLink", "getLink", "transId"]
    missing_keys = [key for key in required_keys if not upload.get(key)]
    if missing_keys:
        raise RuntimeError(f"generatePutLink 缺少字段: {', '.join(missing_keys)}")
    upload.update(
        {
            "taskId": task_id,
            "title": title,
            "contentType": media_content_type(extension),
            "fileSize": file_size,
        }
    )
    return upload


def upload_media(upload, media_path, event_callback=None, cancel_check=None):
    parsed_url = urlsplit(upload["putLink"])
    connection_class = http.client.HTTPSConnection if parsed_url.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed_url.hostname, parsed_url.port, timeout=120)
    request_path = urlunsplit(("", "", parsed_url.path or "/", parsed_url.query, ""))
    file_size = upload["fileSize"]
    connection.putrequest("PUT", request_path, skip_accept_encoding=True)
    connection.putheader("Content-Type", upload["contentType"])
    connection.putheader("Content-Length", str(file_size))
    connection.endheaders()
    uploaded_bytes = 0
    next_report = 10
    print(f"[upload] 开始上传 {media_path.name} ({file_size} bytes)")
    emit_event(
        event_callback,
        "uploading",
        progress=0.0,
        uploadedBytes=0,
        totalBytes=file_size,
        message="开始上传媒体到听悟",
    )
    with open(media_path, "rb") as media_file:
        while True:
            ensure_not_cancelled(cancel_check)
            chunk = media_file.read(8 * 1024 * 1024)
            if not chunk:
                break
            connection.send(chunk)
            uploaded_bytes += len(chunk)
            progress = min(1.0, uploaded_bytes / file_size) if file_size else 1.0
            progress_percent = int(progress * 100)
            emit_event(
                event_callback,
                "uploading",
                progress=progress,
                uploadedBytes=uploaded_bytes,
                totalBytes=file_size,
                message=f"正在上传媒体到听悟（{progress_percent}%）",
            )
            if progress_percent >= next_report:
                print(f"[upload] {progress_percent}%")
                next_report = min(100, next_report + 10)
    response = connection.getresponse()
    response.read()
    connection.close()
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"OSS 上传失败: HTTP {response.status}")
    print(f"[upload] 完成，HTTP {response.status}")
    emit_event(
        event_callback,
        "upload_complete",
        progress=1.0,
        uploadedBytes=uploaded_bytes,
        totalBytes=file_size,
        httpStatus=response.status,
        message="媒体已上传到听悟",
    )


def sync_upload(config, upload):
    tingwu_request(
        config,
        "/api/trans/request?syncPutLink",
        {
            "action": "syncPutLink",
            "version": "1.0",
            "fileLink": upload["getLink"],
            "fileSize": upload["fileSize"],
            "transId": upload["transId"],
        },
    )
    print(f"[task] 上传已同步，transId={upload['transId']}")


def task_summary(trans_id, status, media_path=None):
    return {
        "transId": trans_id,
        "status": status.get("status"),
        "statusMessage": status.get("statusMsg", ""),
        "duration": status.get("duration"),
        "wordCount": status.get("wordCount"),
        "showName": (status.get("tag") or {}).get("showName"),
        "mediaPath": str(media_path) if media_path else None,
        "updatedAt": utc_now(),
    }


def poll_transcription(
    config,
    trans_id,
    task_path,
    media_path,
    poll_interval,
    timeout,
    event_callback=None,
    cancel_check=None,
):
    started_at = time.monotonic()
    last_status_line = None
    poll_count = 0
    while time.monotonic() - started_at < timeout:
        ensure_not_cancelled(cancel_check)
        poll_count += 1
        response = tingwu_request(
            config,
            "/api/trans/request?getTransStatus",
            {
                "action": "getTransStatus",
                "version": "1.0",
                "userId": "",
                "transIds": [trans_id],
                "preview": 1,
            },
        )
        records = response.get("data") if isinstance(response.get("data"), list) else []
        status = next((record for record in records if record.get("transId") == trans_id), records[0] if records else None)
        if status:
            raw_progress = status.get("progress", 0)
            try:
                numeric_progress = float(raw_progress or 0)
            except (TypeError, ValueError):
                numeric_progress = 0.0
            normalized_progress = numeric_progress / 100.0 if numeric_progress > 1 else numeric_progress
            normalized_progress = max(0.0, min(normalized_progress, 1.0))
            status_line = (
                f"status={status.get('status')} progress={raw_progress} "
                f"message={status.get('statusMsg', '')}"
            )
            emit_event(
                event_callback,
                "polling",
                transId=trans_id,
                pollCount=poll_count,
                elapsedSeconds=round(time.monotonic() - started_at, 1),
                remoteStatus=status.get("status"),
                remoteProgress=raw_progress,
                progress=normalized_progress,
                statusMessage=status.get("statusMsg", ""),
                duration=status.get("duration"),
                wordCount=status.get("wordCount"),
                message=f"听悟轮询 #{poll_count}：{status.get('statusMsg') or status.get('status')}",
            )
            if status_line != last_status_line:
                print(f"[task] {status_line}")
                last_status_line = status_line
                write_private_json(task_path, task_summary(trans_id, status, media_path))
            numeric_status = int(status.get("status"))
            if numeric_status == 0:
                return status
            if numeric_status not in {1, 3, 4, 5}:
                raise RuntimeError(f"转写失败: {status_line}")
        else:
            emit_event(
                event_callback,
                "polling",
                transId=trans_id,
                pollCount=poll_count,
                elapsedSeconds=round(time.monotonic() - started_at, 1),
                progress=0.0,
                message=f"听悟轮询 #{poll_count}：暂未返回任务状态",
            )
        time.sleep(poll_interval)
    raise RuntimeError(f"转写等待超时: {timeout} 秒")


def fetch_result(config, trans_id):
    return tingwu_request(
        config,
        "/api/trans/getTransResult",
        {"action": "getTransResult", "version": "1.0", "transId": trans_id},
    )


def parse_remote_result(response):
    raw_result = (response.get("data") or {}).get("result")
    if isinstance(raw_result, str):
        return json.loads(raw_result)
    return raw_result or {"pg": []}


def paragraph_texts(parsed_result):
    paragraphs = []
    for paragraph in parsed_result.get("pg", []):
        text = "".join(str(segment.get("tc", "")) for segment in paragraph.get("sc", []))
        if text:
            paragraphs.append(text)
    return paragraphs


def timed_segments(parsed_result):
    segments = []
    for paragraph in parsed_result.get("pg", []):
        for segment in paragraph.get("sc", []):
            text = str(segment.get("tc", ""))
            if not text:
                continue
            segments.append(
                {
                    "begin": int(segment.get("bt", 0)),
                    "end": int(segment.get("et", segment.get("bt", 0))),
                    "text": text,
                }
            )
    return segments


def build_srt_cues(parsed_result):
    cues = []
    current_segments = []

    def flush_current():
        if not current_segments:
            return
        cues.append(
            {
                "begin": current_segments[0]["begin"],
                "end": max(current_segments[-1]["end"], current_segments[0]["begin"] + 1),
                "text": "".join(segment["text"] for segment in current_segments).strip(),
            }
        )
        current_segments.clear()

    for segment in timed_segments(parsed_result):
        if current_segments and segment["begin"] - current_segments[-1]["end"] > SRT_GAP_MS:
            flush_current()
        current_segments.append(segment)
        cue_text = "".join(item["text"] for item in current_segments)
        cue_duration = current_segments[-1]["end"] - current_segments[0]["begin"]
        punctuation_end = re.search(r"[。！？!?；;]$", segment["text"]) is not None
        if len(cue_text) >= SRT_MAX_CHARS or cue_duration >= SRT_MAX_DURATION_MS or punctuation_end:
            flush_current()
    flush_current()
    return [cue for cue in cues if cue["text"]]


def srt_timestamp(milliseconds):
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def render_srt(cues):
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{srt_timestamp(cue['begin'])} --> {srt_timestamp(cue['end'])}\n{cue['text']}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_outputs(task_dir, output_name, response, trans_id, status, media_path=None):
    parsed_result = parse_remote_result(response)
    text_content = "\n".join(paragraph_texts(parsed_result)) + "\n"
    cues = build_srt_cues(parsed_result)
    text_path = task_dir / f"{output_name}.txt"
    srt_path = task_dir / f"{output_name}.srt"
    task_path = task_dir / "task.json"
    write_private_text(text_path, text_content)
    write_private_text(srt_path, render_srt(cues))
    summary = task_summary(trans_id, status, media_path)
    summary.update({"textFile": text_path.name, "srtFile": srt_path.name, "subtitleCount": len(cues)})
    write_private_json(task_path, summary)
    print(f"[result] 纯文字: {text_path}")
    print(f"[result] 时间轴: {srt_path}")
    print(f"[result] 字幕条目: {len(cues)}")
    return {
        "parsedResult": parsed_result,
        "text": text_content,
        "textPath": str(text_path),
        "srtPath": str(srt_path),
        "taskPath": str(task_path),
        "subtitleCount": len(cues),
    }


def transcribe_media(
    media_path,
    task_dir,
    config_path=DEFAULT_CONFIG_PATH,
    poll_interval=DEFAULT_POLL_INTERVAL,
    timeout=DEFAULT_TIMEOUT,
    event_callback: ProgressCallback | None = None,
    cancel_check=None,
):
    media_path = Path(media_path).expanduser().resolve()
    task_dir = Path(task_dir).expanduser().resolve()
    config_path = Path(config_path).expanduser().resolve()
    if not media_path.is_file():
        raise RuntimeError(f"媒体文件不存在: {media_path}")

    task_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()
    try:
        ensure_not_cancelled(cancel_check)
        config = load_config(config_path)
        emit_event(event_callback, "creating", progress=0.0, message="正在创建听悟转写任务")
        upload = create_upload_task(config, media_path)
        trans_id = upload["transId"]
        write_private_json(
            task_dir / "task.json",
            {
                "transId": trans_id,
                "taskId": upload["taskId"],
                "status": "created",
                "mediaPath": str(media_path),
                "createdAt": utc_now(),
            },
        )
        print(f"[task] 已创建，transId={trans_id}")
        emit_event(
            event_callback,
            "created",
            transId=trans_id,
            progress=0.0,
            message="听悟转写任务已创建",
        )
        upload_media(upload, media_path, event_callback=event_callback, cancel_check=cancel_check)
        ensure_not_cancelled(cancel_check)
        emit_event(
            event_callback,
            "syncing",
            transId=trans_id,
            progress=1.0,
            message="正在同步听悟上传结果",
        )
        sync_upload(config, upload)
        status = poll_transcription(
            config,
            trans_id,
            task_dir / "task.json",
            media_path,
            float(poll_interval),
            float(timeout),
            event_callback=event_callback,
            cancel_check=cancel_check,
        )
        ensure_not_cancelled(cancel_check)
        emit_event(
            event_callback,
            "fetching_result",
            transId=trans_id,
            progress=1.0,
            message="听悟转写完成，正在获取字幕结果",
        )
        response = fetch_result(config, trans_id)
        outputs = write_outputs(task_dir, media_path.stem, response, trans_id, status, media_path)
        result = {
            "transId": trans_id,
            "status": status,
            "elapsedSeconds": round(time.monotonic() - started_at, 3),
            **outputs,
        }
        emit_event(
            event_callback,
            "completed",
            transId=trans_id,
            progress=1.0,
            elapsedSeconds=result["elapsedSeconds"],
            subtitleCount=outputs["subtitleCount"],
            textPath=outputs["textPath"],
            srtPath=outputs["srtPath"],
            message=f"已生成 {outputs['subtitleCount']} 条听悟字幕",
        )
        return result
    except Exception as error:
        emit_event(event_callback, "failed", progress=0.0, message=str(error))
        raise


def check_auth(config, quiet=False):
    response = tingwu_request(
        config,
        "/api/trans/request?getTransList",
        {
            "action": "getTransList",
            "version": "1.0",
            "userId": "",
            "filter": {
                "status": [0, 1, 2, 3, 4, 11],
                "fileTypes": [],
                "beginTime": "",
                "mediaType": "",
                "endTime": "",
                "showName": "",
                "read": "",
                "lang": "",
                "shareUserId": "",
                "client": "",
            },
            "preview": 1,
            "pageNo": 1,
            "pageSize": 1,
        },
    )
    task_count = int(response.get("total", 0) or 0)
    if not quiet:
        print(f"[check] 独立 HTTP 认证有效，任务总数={task_count}")
    return {"taskCount": task_count}


def task_directory_for_media(media_path, task_dir_argument):
    if task_dir_argument:
        return Path(task_dir_argument).expanduser().resolve()
    return DEFAULT_TASK_ROOT.expanduser().resolve() / media_path.stem


def convert_existing(result_json_path, task_dir_argument):
    result_json_path = Path(result_json_path).expanduser().resolve()
    with open(result_json_path, encoding="utf-8") as source:
        response = json.load(source)
    output_name = result_json_path.stem
    if output_name.endswith(".tingwu"):
        output_name = output_name[: -len(".tingwu")]
    task_dir = Path(task_dir_argument).expanduser().resolve() if task_dir_argument else result_json_path.parent / output_name
    task_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = result_json_path.with_name(f"{result_json_path.stem}.task.json")
    metadata = {}
    if metadata_path.is_file():
        with open(metadata_path, encoding="utf-8") as source:
            metadata = json.load(source)
    trans_id = metadata.get("transId") or (response.get("data") or {}).get("transId") or "converted"
    status = {
        "status": metadata.get("status", 0),
        "statusMsg": metadata.get("statusMessage", ""),
        "duration": metadata.get("duration"),
        "wordCount": metadata.get("wordCount"),
        "tag": {"showName": metadata.get("showName", output_name)},
    }
    write_outputs(task_dir, output_name, response, trans_id, status)


def parse_arguments():
    parser = argparse.ArgumentParser(description="通义听悟独立 HTTP 上传、转写与 SRT 生成")
    parser.add_argument("media", nargs="?", default=str(DEFAULT_MEDIA_PATH), help="媒体文件路径")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="独立认证配置路径")
    parser.add_argument("--task-dir", help="任务目录，默认使用媒体文件名对应目录")
    parser.add_argument("--check", action="store_true", help="仅验证独立 HTTP 认证")
    parser.add_argument("--resume", metavar="TASK_DIR", help="读取任务目录中的 task.json 并继续")
    parser.add_argument("--convert", metavar="RESULT_JSON", help="将旧 JSON 结果转换为 TXT 和 SRT")
    parser.add_argument("--import-auth", metavar="REQUEST_JSON", help="从 JS MCP 导出的请求快照更新认证配置")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="轮询间隔秒数")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="最长等待秒数")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    config_path = Path(arguments.config).expanduser().resolve()
    if arguments.import_auth:
        import_auth_config(Path(arguments.import_auth).expanduser().resolve(), config_path)
        return
    if arguments.convert:
        convert_existing(arguments.convert, arguments.task_dir)
        return

    config = load_config(config_path)
    if arguments.check:
        check_auth(config)
        return

    if arguments.resume:
        task_dir = Path(arguments.resume).expanduser().resolve()
        task_path = task_dir / "task.json"
        with open(task_path, encoding="utf-8") as source:
            task = json.load(source)
        trans_id = task["transId"]
        media_path_value = task.get("mediaPath")
        media_path = Path(media_path_value).resolve() if media_path_value else None
        output_name = media_path.stem if media_path else task_dir.name
    else:
        media_path = Path(arguments.media).expanduser().resolve()
        task_dir = task_directory_for_media(media_path, arguments.task_dir)
        transcribe_media(
            media_path,
            task_dir,
            config_path=config_path,
            poll_interval=arguments.poll_interval,
            timeout=arguments.timeout,
        )
        return

    status = poll_transcription(
        config,
        trans_id,
        task_dir / "task.json",
        media_path,
        arguments.poll_interval,
        arguments.timeout,
    )
    response = fetch_result(config, trans_id)
    write_outputs(task_dir, output_name, response, trans_id, status, media_path)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"[error] {error}", file=sys.stderr)
        sys.exit(1)
