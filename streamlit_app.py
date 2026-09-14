"""BTS 투자철학: Bitcoin, Time and read-only Notion Self cards."""
import json
import base64
from html import escape
from pathlib import Path
import socket
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID
from zoneinfo import ZoneInfo


class BoardError(Exception):
    """Only safe, user-facing messages belong in this exception."""


def notion_request(token, path, body=None):
    request = Request(
        "https://api.notion.com/v1/" + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + token,
                 "Notion-Version": "2025-09-03", "Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        messages = {
            400: "기록부 항목 또는 연결 설정을 확인해 주세요.",
            401: "노션 인증을 확인하지 못했습니다. 앱의 비밀 설정을 확인해 주세요.",
            403: "BTS 투자철학 연결의 콘텐츠 읽기 권한을 확인해 주세요.",
            404: "기록부에 접근할 수 없습니다. 연결의 콘텐츠 사용 권한을 확인해 주세요.",
            429: "노션 요청이 잠시 많습니다. 잠시 후 새로고침해 주세요.",
        }
        raise BoardError(messages.get(exc.code, "노션 응답이 원활하지 않습니다. 잠시 후 다시 시도해 주세요.")) from None
    except (URLError, TimeoutError, socket.timeout, ValueError):
        raise BoardError("노션에 연결하지 못했습니다. 잠시 후 새로고침해 주세요.") from None


def plain_text(prop):
    return "".join(part.get("plain_text", part.get("text", {}).get("content", ""))
                   for part in prop.get(prop.get("type"), []) if isinstance(part, dict))


def load_records(token, source_id, request_fn=notion_request):
    try:
        source_id = str(UUID(source_id))
    except (ValueError, TypeError, AttributeError):
        raise BoardError("앱의 기록부 연결 설정을 확인해 주세요.") from None
    source = request_fn(token, "data_sources/" + source_id)
    expected = {"활동명": "title", "보드에 표시": "checkbox", "표시 순서": "number",
                "분야": "select", "상태": "select", "한줄 기록": "rich_text", "다음 목표": "rich_text"}
    if any(source.get("properties", {}).get(name, {}).get("type") != kind
           for name, kind in expected.items()):
        raise BoardError("기록부 항목 구성이 변경되었습니다. 활동명·보드에 표시·표시 순서 등의 항목을 확인해 주세요.")
    body = {"page_size": 100,
            "filter": {"property": "보드에 표시", "checkbox": {"equals": True}},
            "sorts": [{"property": "표시 순서", "direction": "ascending"},
                      {"timestamp": "last_edited_time", "direction": "descending"}]}
    records, cursors = [], set()
    for _ in range(100):
        page = request_fn(token, "data_sources/" + source_id + "/query", body)
        if not isinstance(page.get("results"), list) or "has_more" not in page:
            raise BoardError("노션 조회 결과를 확인하지 못했습니다. 다시 시도해 주세요.")
        for item in page["results"]:
            props = item.get("properties", {})
            if item.get("archived") or item.get("in_trash") or props.get("보드에 표시", {}).get("checkbox") is not True:
                continue
            records.append({
                "title": plain_text(props.get("활동명", {})) or "이름 없는 활동",
                "category": (props.get("분야", {}).get("select") or {}).get("name", ""),
                "status": (props.get("상태", {}).get("select") or {}).get("name", ""),
                "note": plain_text(props.get("한줄 기록", {})),
                "next_goal": plain_text(props.get("다음 목표", {})),
                "order": props.get("표시 순서", {}).get("number"),
                "edited": item.get("last_edited_time", ""),
            })
        if not page["has_more"]:
            # Stable sorts: most recently edited first when display orders tie.
            records.sort(key=lambda row: row["edited"], reverse=True)
            records.sort(key=lambda row: (row["order"] is None, row["order"] if row["order"] is not None else 0))
            return records
        cursor = page.get("next_cursor")
        if not cursor or cursor in cursors:
            raise BoardError("기록 전체를 읽지 못했습니다. 다시 새로고침해 주세요.")
        cursors.add(cursor)
        body = dict(body, start_cursor=cursor)
    raise BoardError("조회할 기록이 너무 많습니다. 보드 표시 대상을 줄여 주세요.")



from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from math import isfinite
import math
import xml.etree.ElementTree as ET

UPBIT_URL = "https://api.upbit.com/v1/ticker?markets=KRW-BTC"
COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/ticker"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
MAX_BYTES = 1_000_000
HALVING_INTERVAL = 210_000
SATOSHIS_PER_BTC = 100_000_000
TARGET_BLOCK_SECONDS = 600
TIME_ENDPOINTS = {
    "recent_blocks": "https://mempool.space/api/v1/blocks",
    "pending_blocks": "https://mempool.space/api/v1/fees/mempool-blocks",
    "fees": "https://mempool.space/api/v1/fees/recommended",
    "backlog": "https://mempool.space/api/mempool",
}


def fetch_bytes(url, timeout=15):
    request = Request(url, headers={"User-Agent": "BTS-investment-philosophy/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        raise ValueError("공개 데이터 제공처에 연결할 수 없습니다.") from None
    if len(body) > MAX_BYTES:
        raise ValueError("공개 데이터 응답 크기를 확인할 수 없습니다.")
    return body


def _positive(value):
    if isinstance(value, bool):
        raise ValueError("가격 데이터 형식을 확인할 수 없습니다.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("가격 데이터 형식을 확인할 수 없습니다.") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError("가격 데이터 범위를 확인할 수 없습니다.")
    return number


def _json_body(body):
    try:
        return json.loads(body)
    except (ValueError, TypeError, UnicodeDecodeError):
        raise ValueError("공개 데이터 응답을 해석할 수 없습니다.") from None


def parse_upbit(payload):
    if not isinstance(payload, list):
        raise ValueError("업비트 응답 형식을 확인할 수 없습니다.")
    wanted = {"KRW-BTC": "BTC"}
    result = {}
    for item in payload:
        if not isinstance(item, dict) or item.get("market") not in wanted:
            continue
        symbol = wanted[item["market"]]
        if symbol in result:
            raise ValueError("업비트 거래쌍이 중복되었습니다.")
        price = _positive(item.get("trade_price"))
        milliseconds = _positive(item.get("trade_timestamp"))
        try:
            trade_time = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            raise ValueError("업비트 체결시각을 확인할 수 없습니다.") from None
        result[symbol] = {"price": price, "time": trade_time}
    if set(result) != {"BTC"}:
        raise ValueError("업비트 거래쌍 데이터가 일부 누락되었습니다.")
    return result


def parse_usd(payload):
    if not isinstance(payload, dict):
        raise ValueError("달러 시세 응답 형식을 확인할 수 없습니다.")
    price = _positive(payload.get("price"))
    raw_time = payload.get("time")
    if not isinstance(raw_time, str):
        raise ValueError("달러 시세 체결시각이 없습니다.")
    try:
        trade_time = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("달러 시세 체결시각을 확인할 수 없습니다.") from None
    if trade_time.tzinfo is None:
        raise ValueError("달러 시세 체결시각의 시간대가 없습니다.")
    return {"price": price, "time": trade_time.astimezone(timezone.utc)}


def parse_fx(body):
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError("ECB 환율 응답을 해석할 수 없습니다.") from None
    dated = [item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "Cube" and "time" in item.attrib]
    if len(dated) != 1:
        raise ValueError("ECB 환율 기준일을 확인할 수 없습니다.")
    day = dated[0]
    try:
        reference_date = date.fromisoformat(day.attrib["time"])
    except ValueError:
        raise ValueError("ECB 환율 기준일 형식을 확인할 수 없습니다.") from None
    rates = {}
    for item in day:
        currency = item.attrib.get("currency")
        if currency in {"USD", "KRW"}:
            if currency in rates:
                raise ValueError("ECB 통화 데이터가 중복되었습니다.")
            rates[currency] = _positive(item.attrib.get("rate"))
    if set(rates) != {"USD", "KRW"}:
        raise ValueError("ECB 달러 또는 원화 환율이 누락되었습니다.")
    return {"rate": rates["KRW"] / rates["USD"], "date": reference_date}


def load_upbit(fetch=fetch_bytes):
    return parse_upbit(_json_body(fetch(UPBIT_URL)))


def load_usd(product, fetch=fetch_bytes):
    if product not in {"BTC-USD"}:
        raise ValueError("지원하지 않는 달러 거래쌍입니다.")
    return parse_usd(_json_body(fetch(COINBASE_URL.format(product=product))))


def load_fx(fetch=fetch_bytes):
    return parse_fx(fetch(ECB_URL))


def _time_number(value, *, integer=False, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid numeric response")
    if not isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError("Invalid numeric response")
    if integer and int(value) != value:
        raise ValueError("Invalid integer response")
    return int(value) if integer else float(value)


def subsidy_satoshis(height):
    height = _time_number(height, integer=True)
    halvings = height // HALVING_INTERVAL
    return 0 if halvings >= 64 else (50 * SATOSHIS_PER_BTC) >> halvings


def halving_status(height, now=None):
    height = _time_number(height, integer=True)
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Timezone-aware query time required")
    now = now.astimezone(timezone.utc)
    cycle = height // HALVING_INTERVAL
    cycle_start = cycle * HALVING_INTERVAL
    next_height = (cycle + 1) * HALVING_INTERVAL
    remaining = next_height - height
    current_sats = subsidy_satoshis(height)
    next_sats = subsidy_satoshis(next_height)
    return {
        "height": height, "halvings_completed": cycle,
        "cycle_start_height": cycle_start, "next_height": next_height,
        "remaining_blocks": remaining,
        "cycle_progress": (height - cycle_start) / HALVING_INTERVAL,
        "current_subsidy_sats": current_sats, "next_subsidy_sats": next_sats,
        "current_subsidy_btc": current_sats / SATOSHIS_PER_BTC,
        "next_subsidy_btc": next_sats / SATOSHIS_PER_BTC,
        "estimated_at": now + timedelta(seconds=remaining * TARGET_BLOCK_SECONDS),
        "estimate_base_at": now, "assumed_seconds_per_block": TARGET_BLOCK_SECONDS,
    }


def parse_recent_blocks(payload):
    if not isinstance(payload, list) or not payload:
        raise ValueError("Missing block response")
    blocks = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise ValueError("Invalid block response")
        if raw.get("stale") is True:
            continue
        block_id = raw.get("id")
        if (not isinstance(block_id, str) or len(block_id) != 64
                or any(char not in "0123456789abcdef" for char in block_id)):
            raise ValueError("Invalid block identifier")
        timestamp = _time_number(raw.get("timestamp"), integer=True, positive=True)
        extras = raw.get("extras") or {}
        if not isinstance(extras, dict):
            raise ValueError("Invalid block extras")
        median_fee = extras.get("medianFee")
        blocks.append({
            "id": block_id, "height": _time_number(raw.get("height"), integer=True),
            "timestamp": timestamp, "mined_at": datetime.fromtimestamp(timestamp, timezone.utc),
            "tx_count": _time_number(raw.get("tx_count"), integer=True),
            "size_bytes": _time_number(raw.get("size"), integer=True),
            "weight_wu": _time_number(raw.get("weight"), integer=True),
            "median_fee_sat_vb": None if median_fee is None else _time_number(median_fee),
            "url": "https://mempool.space/block/" + block_id,
        })
    if not blocks:
        raise ValueError("No active-chain block response")
    return sorted(blocks, key=lambda item: item["height"], reverse=True)


def parse_pending_blocks(payload):
    if not isinstance(payload, list):
        raise ValueError("Invalid projected-block response")
    blocks = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise ValueError("Invalid projected-block response")
        ranges = raw.get("feeRange")
        if not isinstance(ranges, list) or not ranges:
            raise ValueError("Missing projected fee range")
        ranges = [_time_number(value) for value in ranges]
        vsize = _time_number(raw.get("blockVSize"))
        blocks.append({
            "position": index + 1,
            "tx_count": _time_number(raw.get("nTx"), integer=True),
            "vsize_vb": vsize, "size_bytes": _time_number(raw.get("blockSize"), integer=True),
            "median_fee_sat_vb": _time_number(raw.get("medianFee")),
            "min_fee_sat_vb": min(ranges), "max_fee_sat_vb": max(ranges),
            "is_aggregate": vsize > 1_000_000,
        })
    return blocks


def parse_recommended_fees(payload):
    if not isinstance(payload, dict):
        raise ValueError("Invalid fee response")
    return {key: _time_number(payload.get(key)) for key in (
        "fastestFee", "halfHourFee", "hourFee", "economyFee", "minimumFee")}


def parse_backlog(payload):
    if not isinstance(payload, dict):
        raise ValueError("Invalid mempool response")
    return {"tx_count": _time_number(payload.get("count"), integer=True),
            "vsize_vb": _time_number(payload.get("vsize"), integer=True),
            "total_fees_sats": _time_number(payload.get("total_fee"), integer=True)}


def fetch_time_snapshot(public_fetch, now=None):
    fetched_at = now or datetime.now(timezone.utc)
    if not isinstance(fetched_at, datetime) or fetched_at.tzinfo is None:
        raise ValueError("Timezone-aware query time required")
    parsers = {"recent_blocks": parse_recent_blocks, "pending_blocks": parse_pending_blocks,
               "fees": parse_recommended_fees, "backlog": parse_backlog}
    labels = {"recent_blocks": "최근 채굴 블록", "pending_blocks": "예상 대기 블록",
              "fees": "권장 수수료", "backlog": "멤풀 대기 현황"}
    result = {key: None for key in TIME_ENDPOINTS}
    result.update({"halving": None, "errors": {}, "fetched_at": fetched_at,
                   "sources": dict(TIME_ENDPOINTS)})
    def attempt(item):
        key, endpoint = item
        try:
            return key, parsers[key](public_fetch(endpoint)), None
        except Exception:
            return key, None, labels[key] + "을 불러오지 못했습니다. 잠시 후 새로고침해 주세요."
    with ThreadPoolExecutor(max_workers=4) as pool:
        for key, data, error in pool.map(attempt, TIME_ENDPOINTS.items()):
            result[key] = data
            if error is not None:
                result["errors"][key] = error
    if result["recent_blocks"]:
        result["halving"] = halving_status(result["recent_blocks"][0]["height"], fetched_at)
    return result



def public_json(url):
    return _json_body(fetch_bytes(url))


def collect_public(jobs):
    def attempt(item):
        name, loader = item
        try:
            data = loader()
            return name, {"data": data, "checked_at": datetime.now(timezone.utc)}
        except Exception:
            return name, {"data": None, "checked_at": None}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return dict(pool.map(attempt, jobs.items()))


def _compact_integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        valid = isfinite(value) and value >= 0 and int(value) == value
    except (ValueError, OverflowError):
        return None
    if not valid:
        return None
    return int(value)


def block_track(snapshot, previous_height=None):
    """Render real mined blocks and pool projections with gentle motion."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    previous_height = _compact_integer(previous_height)
    mined = {}
    recent_rows = snapshot.get("recent_blocks")
    for block in recent_rows if isinstance(recent_rows, (list, tuple)) else []:
        if not isinstance(block, dict):
            continue
        height = _compact_integer(block.get("height"))
        if height is not None:
            mined[height] = block
    pending = {}
    pending_rows = snapshot.get("pending_blocks")
    for block in pending_rows if isinstance(pending_rows, (list, tuple)) else []:
        if not isinstance(block, dict):
            continue
        position = _compact_integer(block.get("position"))
        count = _compact_integer(block.get("tx_count"))
        if position is not None and position > 0 and count is not None:
            pending[position] = (count, block.get("is_aggregate") is True)

    cards = []
    for height in sorted(mined, reverse=True)[:3][::-1]:
        entrance = " bts-cube-new" if previous_height is not None and height > previous_height else ""
        cards.append(
            f'<div class="bts-cube-slot"><div class="bts-cube bts-cube-mined{entrance}" '
            f'role="img" aria-label="확정 블록 {height:,}">'
            '<i class="bts-cube-top" aria-hidden="true"></i>'
            '<i class="bts-cube-right" aria-hidden="true"></i>'
            f'<div class="bts-cube-front"><span class="bts-cube-mark" aria-hidden="true">✓</span>'
            f'<strong>#{height:,}</strong></div></div></div>'
        )
    if cards and pending:
        cards.append('<span class="bts-cube-divider" aria-hidden="true"></span>')
    for index, position in enumerate(sorted(pending)[:3]):
        count, aggregate = pending[position]
        qualifier = "대기 묶음" if aggregate else "대기"
        cards.append(
            f'<div class="bts-cube-slot"><div class="bts-cube bts-cube-pending" '
            f'style="--bts-phase:{-index * 1.3}s" role="img" '
            f'aria-label="{position}번째 {qualifier}, 거래 {count:,}건">'
            '<i class="bts-cube-top" aria-hidden="true"></i>'
            '<i class="bts-cube-right" aria-hidden="true"></i>'
            f'<div class="bts-cube-front"><span class="bts-cube-mark">{qualifier}</span>'
            f'<strong>{count:,}</strong><small>tx</small></div></div></div>'
        )
    if not cards:
        cards.append('<span class="bts-cubes-empty" role="status">—</span>')
    return '''<style>
    .bts-cubes-wrap{container-type:inline-size;width:100%;overflow:hidden}
    .bts-cubes{display:flex;align-items:center;gap:clamp(3px,1.2%,12px);width:100%;max-width:700px;min-height:132px;padding:29px 4px 19px;box-sizing:border-box}
    .bts-cube-slot{flex:1 1 0;min-width:0;max-width:104px}
    .bts-cube{position:relative;width:77%;aspect-ratio:1;isolation:isolate;filter:drop-shadow(2px 7px 5px #0d294819);transform-origin:center;--bts-front:#3979b6;--bts-top:#75acd9;--bts-right:#245582}
    .bts-cube-front{position:absolute;inset:0;background:linear-gradient(145deg,var(--bts-front),var(--bts-right));border:1px solid #ffffff24;display:flex;flex-direction:column;justify-content:center;align-items:center;color:#fff;box-sizing:border-box;gap:3px;z-index:3}
    .bts-cube-top{position:absolute;bottom:100%;left:0;width:100%;height:27%;background:linear-gradient(100deg,var(--bts-top),var(--bts-front));transform:skewX(-45deg);transform-origin:left bottom;border-top:1px solid #ffffff44;box-sizing:border-box}
    .bts-cube-right{position:absolute;left:100%;top:0;width:27%;height:100%;background:var(--bts-right);transform:skewY(-45deg);transform-origin:left top;border-right:1px solid #0c203433;box-sizing:border-box}
    .bts-cube-front strong{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:clamp(8px,1.7cqw,13px);font-weight:600;letter-spacing:-.055em;white-space:nowrap;line-height:1.25}
    .bts-cube-mark{font:500 clamp(8px,1.5cqw,11px)/1.1 system-ui,sans-serif;opacity:.85}
    .bts-cube-front small{font:500 clamp(7px,1.3cqw,10px)/1 system-ui,sans-serif;opacity:.75}
    .bts-cube-pending{--bts-front:#d4a348;--bts-top:#f2d390;--bts-right:#a77426;animation:bts-pool-float 5s ease-in-out var(--bts-phase,0s) infinite}
    .bts-cube-divider{flex:0 0 1px;height:54px;background:linear-gradient(transparent,#a9aebb,transparent);margin:0 5px}
    .bts-cube-new{animation:bts-confirm-in .9s cubic-bezier(.2,.75,.25,1) both}
    .bts-cubes-empty{font-size:16px;color:#8a8f9b;padding:20px 0}
    @keyframes bts-confirm-in{from{opacity:.25;transform:translateX(30%) translateY(-7px)}to{opacity:1;transform:translateX(0) translateY(0)}}
    @keyframes bts-pool-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}
    @container (max-width:400px){.bts-cubes{min-height:104px;padding-top:23px;padding-bottom:19px}.bts-cube-front{gap:2px}.bts-cube-divider{margin:0 3px;height:38px}}
    @media(prefers-reduced-motion:reduce){.bts-cube-pending,.bts-cube-new{animation:none}}
    </style><div class="bts-cubes-wrap"><div class="bts-cubes" aria-label="비트코인 확정 블록과 멤풀 대기 거래">''' + "".join(cards) + "</div></div>"


COMPACT_STYLE = '''<style>
.block-container{padding-top:2rem!important;padding-bottom:2rem!important}
.bts-market{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:16px;margin:8px 0 12px}
.bts-price-card{border:1px solid rgba(128,140,158,.22);border-radius:16px;padding:22px 24px;background:rgba(128,140,158,.025);min-width:0}
.bts-coin{display:flex;align-items:center;gap:9px;font-size:14px;opacity:.72;margin-bottom:16px}
.bts-coin b{color:#db9117;font-size:21px;opacity:1}
.bts-quotes{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.bts-price{display:flex;align-items:baseline;gap:8px;white-space:nowrap;font-variant-numeric:tabular-nums}
.bts-price .currency{font-size:20px;color:#a98345}.bts-price strong{font-size:clamp(21px,2.5vw,34px);font-weight:550;letter-spacing:-.7px}
.bts-fx{font-size:clamp(19px,2.2vw,29px);font-weight:500;white-space:nowrap;letter-spacing:-.5px}
.bts-reference{display:flex;flex-wrap:wrap;gap:8px 22px;font-size:12px;line-height:1.8;opacity:.67;font-variant-numeric:tabular-nums;margin:5px 0}
.bts-reference b{font-size:13px;font-weight:550}.bts-reference span{display:inline-block}
.bts-progress{height:3px;background:rgba(150,160,175,.14);border-radius:5px;overflow:hidden;margin:12px 0 7px}
.bts-progress i{display:block;height:100%;background:#b89655}
@media(max-width:700px){.bts-market{grid-template-columns:1fr}.bts-price-card{padding:18px 20px}.bts-price strong{font-size:28px}}
@media(max-width:440px){.bts-quotes{grid-template-columns:1fr;gap:12px}.bts-reference{gap:5px 14px}.bts-price strong{font-size:29px}}
</style>'''


BRAND_STYLE = '''<style>
.stApp .bts-section{font-size:26px;font-weight:500;line-height:1.3;letter-spacing:-.6px;margin:1.15rem 0 .85rem;padding:0 0 12px;border-bottom:1px solid rgba(128,140,158,.27);color:inherit}
.stApp .bts-section .bts-section-initial{font-size:2em;font-weight:760;line-height:.9;letter-spacing:-1.6px;vertical-align:baseline}
.bts-section-b .bts-section-initial{color:#e88c23}
.bts-section-t .bts-section-initial{color:#597889}
.bts-section-s .bts-section-initial{color:#548255}
.bts-record-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;align-items:start;margin:12px 0 18px}
.bts-record-card{--bts-tint:rgba(128,140,158,.07);--bts-edge:rgba(128,140,158,.22);min-width:0;border:1px solid var(--bts-edge);border-radius:12px;padding:20px;background:var(--bts-tint);color:inherit}
.bts-record-reading{--bts-tint:rgba(221,177,87,.13);--bts-edge:rgba(190,148,59,.24)}
.bts-record-fitness{--bts-tint:rgba(111,161,107,.12);--bts-edge:rgba(101,151,96,.24)}
.bts-record-project{--bts-tint:rgba(93,145,188,.12);--bts-edge:rgba(88,139,179,.24)}
.stApp .bts-record-title{font-size:19.6px;font-weight:600;line-height:1.45;letter-spacing:-.35px;color:inherit;margin:0 0 12px;padding:0;overflow-wrap:anywhere;word-break:keep-all}
.bts-record-meta{display:flex;align-items:center;flex-wrap:wrap;gap:6px 10px;margin-bottom:12px;font-size:12px;line-height:1.5}
.bts-record-category{display:inline-block;padding:3px 8px;border-radius:4px;background:var(--bts-tint);border:1px solid var(--bts-edge)}
.bts-record-status{opacity:.65}
.bts-record-note,.bts-record-goal{font-size:15px;line-height:1.7;white-space:pre-line;overflow-wrap:anywhere;word-break:keep-all;margin:0}
.bts-record-goal-label{font-size:11px;opacity:.6;margin:15px 0 4px}
@media(max-width:850px){.bts-record-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:580px){.bts-record-grid{grid-template-columns:1fr}.bts-record-card{padding:18px}}
</style>'''


def section_heading(st, section):
    """Render the exact philosophy phrase with a double-size B, T or S."""
    prefix, initial, tail, slug = {
        "B": ("save", "B", "itcoin", "b-bitcoin"),
        "T": ("trust", "T", "ime", "t-time"),
        "S": ("grow", "S", "elf", "s-self"),
    }[section]
    st.markdown(
        f'<h2 class="bts-section bts-section-{section.lower()}" id="{slug}">'
        f'{prefix} <span class="bts-section-initial">{initial}</span>{tail}</h2>',
        unsafe_allow_html=True,
    )


def render_record_cards(st, records):
    """Keep record order and content while escaping all Notion-authored HTML."""
    styles = {"독서": "reading", "운동": "fitness", "프로젝트": "project"}
    cards = []
    for record in records:
        category = str(record.get("category") or "")
        status = str(record.get("status") or "")
        title = escape(str(record.get("title") or "이름 없는 활동"))
        note = str(record.get("note") or "")
        goal = str(record.get("next_goal") or "")
        style = styles.get(category.strip(), "neutral")
        content = [f'<article class="bts-record-card bts-record-{style}">',
                   f'<h3 class="bts-record-title">{title}</h3>']
        if category or status:
            content.append('<div class="bts-record-meta">')
            if category:
                content.append(f'<span class="bts-record-category">{escape(category)}</span>')
            if status:
                content.append(f'<span class="bts-record-status">{escape(status)}</span>')
            content.append('</div>')
        if note:
            content.append(f'<p class="bts-record-note">{escape(note)}</p>')
        if goal:
            content.append('<div class="bts-record-goal-label">다음 목표</div>')
            content.append(f'<p class="bts-record-goal">{escape(goal)}</p>')
        content.append('</article>')
        cards.append(''.join(content))
    if cards:
        st.markdown('<div class="bts-record-grid">' + ''.join(cards) + '</div>',
                    unsafe_allow_html=True)


def render_cover(st):
    """Use the supplied, unmodified artwork as an accessible entry button."""
    cover = Path(__file__).with_name("bts-cover.png")
    if not cover.is_file():
        return False
    if st.session_state.get("board_open"):
        return False
    image_data = base64.b64encode(cover.read_bytes()).decode("ascii")
    st.markdown(f'''<style>
.stApp{{background:#fbf8f0}}
.block-container{{max-width:1700px;padding:10vh 16px 4vh!important}}
.bts-cover-name{{text-align:center;font-size:15px;letter-spacing:.06em;color:#34434a;margin:0 0 24px}}
.st-key-enter_dashboard button{{display:block;width:100%;height:auto;aspect-ratio:3/1;min-height:0;padding:0;border:0;border-radius:0;background:#fbf8f0 url("data:image/png;base64,{image_data}") center/contain no-repeat;box-shadow:none;cursor:pointer;transition:filter .25s ease}}
.st-key-enter_dashboard button:hover{{background-color:#fbf8f0;filter:brightness(1.035);border:0}}
.st-key-enter_dashboard button:focus-visible{{outline:3px solid #d28b2b;outline-offset:6px}}
.st-key-enter_dashboard button p{{opacity:0}}
.bts-cover-hint{{text-align:center;font-size:13px;letter-spacing:.04em;color:#526057;margin:24px 0 0}}
@media(max-width:600px){{.block-container{{padding-top:17vh!important}}.bts-cover-name{{font-size:14px;margin-bottom:28px}}}}
@media(prefers-reduced-motion:reduce){{.st-key-enter_dashboard button{{transition:none}}}}
</style><p class="bts-cover-name">BTS 투자철학</p>''', unsafe_allow_html=True)
    if st.button("대시보드 열기", key="enter_dashboard", use_container_width=True):
        st.session_state["board_open"] = True
        st.rerun()
    st.markdown('<p class="bts-cover-hint">대시보드 열기 →</p>', unsafe_allow_html=True)
    return True



def visible_quote(result, symbol=None):
    data = result.get("data")
    if data is None:
        return None
    quote = data[symbol] if symbol else data
    age = (datetime.now(timezone.utc) - quote["time"]).total_seconds()
    return quote["price"] if -300 <= age <= 900 else None


def render_market(st, quotes, reference):
    section_heading(st, "B")
    krw = visible_quote(quotes["upbit"], "BTC")
    usd = visible_quote(quotes["BTC"])
    fx = reference["fx"].get("data")
    if fx and not 0 <= (datetime.now(timezone.utc).date() - fx["date"]).days <= 7:
        fx = None
    krw_text = "—" if krw is None else f"{krw:,.0f}"
    usd_text = "—" if usd is None else f"{usd:,.2f}"
    fx_text = "—" if fx is None else f"{fx['rate']:,.2f}"
    st.markdown(f'''<div class="bts-market">
<div class="bts-price-card"><div class="bts-coin"><b>₿</b> Bitcoin</div>
<div class="bts-quotes"><div class="bts-price"><span class="currency" aria-label="KRW">₩</span><strong>{krw_text}</strong></div>
<div class="bts-price"><span class="currency" aria-label="USD">$</span><strong>{usd_text}</strong></div></div></div>
<div class="bts-price-card"><div class="bts-coin">$ / ₩</div><div class="bts-fx">$1 = ₩{fx_text}</div></div>
</div>''', unsafe_allow_html=True)


def render_time(st, snapshot):
    section_heading(st, "T")
    previous_height = st.session_state.get("bts_previous_height")
    st.markdown(block_track(snapshot, previous_height), unsafe_allow_html=True)
    if snapshot["recent_blocks"]:
        st.session_state["bts_previous_height"] = snapshot["recent_blocks"][0]["height"]
    parts = []
    backlog, fees = snapshot["backlog"], snapshot["fees"]
    if backlog is not None:
        parts.append(f"<span>대기 <b>{backlog['tx_count']:,}</b> · {backlog['vsize_vb']/1_000_000:.2f} MvB</span>")
    if fees is not None:
        parts.append(f"<span>수수료 · 빠름 <b>{fees['fastestFee']:g}</b> / 30분 <b>{fees['halfHourFee']:g}</b> / 1시간 <b>{fees['hourFee']:g}</b> sat/vB</span>")
    if snapshot["errors"]:
        parts.append("<span>일부 연결 지연</span>")
    if parts:
        st.markdown('<div class="bts-reference">'+"".join(parts)+'</div>', unsafe_allow_html=True)
    data = snapshot["halving"]
    if data is not None:
        progress = min(data["cycle_progress"] * 100, 99.99)
        estimated = data["estimated_at"].astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d")
        st.markdown(f'''<div class="bts-progress" role="progressbar" aria-label="Halving" aria-valuenow="{progress:.2f}" aria-valuemin="0" aria-valuemax="100"><i style="width:{progress:.4f}%"></i></div>
<div class="bts-reference"><span>반감기 <b>{progress:.2f}%</b></span><span>남은 블록 <b>{data['remaining_blocks']:,}</b></span><span>다음 <b>#{data['next_height']:,}</b></span><span>예상 <b>{estimated}</b></span><span><b>{data['current_subsidy_btc']:g} → {data['next_subsidy_btc']:g}</b> BTC/블록</span></div>''', unsafe_allow_html=True)


def render_self(st):
    section_heading(st, "S")
    try:
        token = str(st.secrets.get("NOTION_TOKEN", "")).strip()
        source_id = str(st.secrets.get("NOTION_DATA_SOURCE_ID", "")).strip()
    except Exception:
        st.caption("기록 연결 대기")
        return
    if not token or not source_id:
        st.caption("기록 연결 대기")
        return
    try:
        records = load_records(token, source_id)
    except Exception:
        st.caption("기록 연결 지연 · ↻")
        return
    if not records:
        st.caption("표시할 기록이 없습니다.")
        return
    render_record_cards(st, records)


def main():
    import streamlit as st
    st.set_page_config(page_title="BTS 투자철학", page_icon="🌱", layout="wide")
    st.markdown(COMPACT_STYLE + BRAND_STYLE, unsafe_allow_html=True)
    if render_cover(st):
        return

    @st.cache_data(ttl=55, show_spinner=False, max_entries=1)
    def bitcoin_quotes():
        return collect_public({"upbit": load_upbit, "BTC": lambda: load_usd("BTC-USD")})

    @st.cache_data(ttl=3600, show_spinner=False, max_entries=1)
    def exchange_reference():
        return collect_public({"fx": load_fx})

    @st.cache_data(ttl=55, show_spinner=False, max_entries=1)
    def time_values():
        return fetch_time_snapshot(public_json)

    heading, refresh = st.columns([10, 1])
    with heading:
        st.title("BTS 투자철학")
    with refresh:
        if st.button("↻", help="새로고침", key="refresh_board"):
            bitcoin_quotes.clear()
            exchange_reference.clear()
            time_values.clear()
    st.caption("save Bitcoin · trust Time · grow Self")

    @st.fragment(run_every=60)
    def public_sections():
        render_market(st, bitcoin_quotes(), exchange_reference())
        render_time(st, time_values())

    public_sections()
    render_self(st)
    if Path(__file__).with_name("bts-cover.png").is_file():
        if st.button("← 처음으로", key="back_to_cover"):
            st.session_state["board_open"] = False
            st.rerun()


if __name__ == "__main__":
    main()
