"""BTS 투자철학: Bitcoin, Time and read-only Notion Self cards."""
import json
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
from html.parser import HTMLParser
from math import isfinite
import math
import re
import xml.etree.ElementTree as ET

UPBIT_URL = "https://api.upbit.com/v1/ticker?markets=KRW-BTC,KRW-USDT"
COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/ticker"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
BOK_URL = "https://www.bok.or.kr/portal/singl/baseRate/list.do?dataSeCd=01&menuNo=200643"
NYFED_URL = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"
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
    wanted = {"KRW-BTC": "BTC", "KRW-USDT": "USDT"}
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
    if set(result) != {"BTC", "USDT"}:
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
    if product not in {"BTC-USD", "USDT-USD"}:
        raise ValueError("지원하지 않는 달러 거래쌍입니다.")
    return parse_usd(_json_body(fetch(COINBASE_URL.format(product=product))))


def load_fx(fetch=fetch_bytes):
    return parse_fx(fetch(ECB_URL))


def checked_rate(value):
    if isinstance(value, bool):
        raise ValueError("Invalid policy rate")
    number = float(value)
    if not math.isfinite(number) or not -5 <= number <= 40:
        raise ValueError("Invalid policy rate")
    return number


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.stack = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.stack.append([])
        elif tag == "tr" and self.stack:
            self.row = []
        elif tag in {"th", "td"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.stack[-1].append(self.row)
            self.row = None
        elif tag == "table" and self.stack:
            self.tables.append(self.stack.pop())


def parse_bok(document, today=None):
    today = today or date.today()
    parser = TableParser()
    parser.feed(document)
    for rows in parser.tables:
        headers = " ".join(" ".join(row) for row in rows[:3])
        if "변경일자" not in headers or "기준금리" not in headers:
            continue
        observations = []
        previous_year = None
        for row in rows:
            if len(row) == 3 and re.fullmatch(r"\d{4}", row[0]):
                previous_year, month_day, value = int(row[0]), row[1], row[2]
            elif len(row) == 2 and previous_year is not None:
                month_day, value = row
            else:
                continue
            match = re.fullmatch(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", month_day)
            if not match:
                continue
            observed_date = date(previous_year, int(match[1]), int(match[2]))
            if observed_date <= today:
                observations.append((observed_date, checked_rate(value)))
        if observations:
            observed_date, rate = max(observations)
            return {"rate": rate, "date": observed_date.isoformat(), "date_kind": "last_change", "source": BOK_URL}
    raise ValueError("BOK policy-rate table not found")


def parse_nyfed(payload, today=None):
    today = today or date.today()
    observations = []
    for row in payload.get("refRates", []):
        if row.get("type") != "EFFR":
            continue
        observed_date = date.fromisoformat(row["effectiveDate"])
        low, high = checked_rate(row["targetRateFrom"]), checked_rate(row["targetRateTo"])
        if low > high:
            raise ValueError("NY Fed target range reversed")
        if observed_date <= today:
            observations.append((observed_date, low, high))
    if not observations:
        raise ValueError("No target-range observations")
    observed_date, low, high = max(observations)
    return {"low": low, "high": high, "date": observed_date.isoformat(), "date_kind": "observation", "source": "https://www.newyorkfed.org/markets/reference-rates/effr", "stale": (today - observed_date).days > 5}


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



BIS_URL = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.KR?lastNObservations=1"
BIS_SOURCE_URL = "https://data.bis.org/topics/CBPOL/BIS%2CWS_CBPOL%2C1.0/D.KR"


def parse_bis(body, today=None):
    today = today or date.today()
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError, TypeError):
        raise ValueError("BIS 금리 자료 형식을 확인할 수 없습니다.") from None
    references = [item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "Ref"]
    if not any(item.get("agencyID") == "BIS" and item.get("id") == "WS_CBPOL" for item in references):
        raise ValueError("BIS 기준금리 자료인지 확인할 수 없습니다.")
    datasets = [item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "DataSet"]
    matches = []
    for dataset in datasets:
        for series in dataset:
            if (series.tag.rsplit("}", 1)[-1] == "Series"
                    and series.get("FREQ") == "D" and series.get("REF_AREA") == "KR"):
                matches.append((dataset, series))
    if len(matches) != 1:
        raise ValueError("BIS 한국 일별 기준금리 자료가 없거나 중복되었습니다.")
    dataset, series = matches[0]
    if dataset.get("UNIT_MULT") != "0" or dataset.get("UNIT_MEASURE") != "368":
        raise ValueError("BIS 금리 단위를 확인할 수 없습니다.")
    observations = {}
    for observation in series:
        if observation.tag.rsplit("}", 1)[-1] != "Obs":
            continue
        for node in (series, observation):
            if (node.get("UNIT_MULT", "0") != "0"
                    or node.get("UNIT_MEASURE", "368") != "368"):
                raise ValueError("BIS 금리 단위가 일치하지 않습니다.")
        try:
            observed_date = date.fromisoformat(observation.attrib["TIME_PERIOD"])
            rate = float(observation.attrib["OBS_VALUE"])
        except (KeyError, ValueError, TypeError, OverflowError):
            raise ValueError("BIS 금리 값 또는 기준일을 확인할 수 없습니다.") from None
        if observed_date > today or not math.isfinite(rate) or not -5 <= rate <= 40:
            raise ValueError("BIS 금리 값 또는 기준일의 범위를 확인할 수 없습니다.")
        if observed_date in observations:
            raise ValueError("BIS 금리 기준일이 중복되었습니다.")
        observations[observed_date] = rate
    if not observations:
        raise ValueError("BIS 금리 관측값이 없습니다.")
    latest = max(observations)
    return {"rate": observations[latest], "date": latest.isoformat(),
            "date_kind": "observation", "source": BIS_SOURCE_URL,
            "provider": "BIS", "stale": (today - latest).days > 10}


def public_json(url):
    return _json_body(fetch_bytes(url))


def load_bok():
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return parse_bok(fetch_bytes(BOK_URL, timeout=5).decode("utf-8"), today=today)
    except Exception:
        return parse_bis(fetch_bytes(BIS_URL), today=today)


def load_us_policy():
    return parse_nyfed(public_json(NYFED_URL), today=datetime.now(timezone.utc).date())


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


def kst_time(value):
    return value.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST")


def public_missing(st, label):
    st.info(f"{label} 자료를 불러오지 못했습니다. 잠시 후 ‘자료 새로고침’을 눌러 주세요.")


def quote_detail(st, result, symbol=None):
    if result["data"] is None:
        return
    quote = result["data"][symbol] if symbol else result["data"]
    st.caption(f"최근 체결 {kst_time(quote['time'])}")
    st.caption(f"조회 {kst_time(result['checked_at'])}")
    age = (datetime.now(timezone.utc) - quote["time"]).total_seconds()
    if age > 900:
        st.warning("최근 체결시각이 15분 이상 지났습니다. 출처에서 현재 시세를 확인해 주세요.")
    elif age < -300:
        st.warning("출처의 체결시각이 조회시각보다 앞서 있습니다. 시각을 확인해 주세요.")


def render_self(st):
    st.header("S · Self", divider="gray")
    st.write("오늘의 노력을 기록하고, 다음 걸음을 이어갑니다.")
    st.button("기록 새로고침", help="노션에서 보드에 표시한 기록을 다시 읽습니다.")
    try:
        token = str(st.secrets.get("NOTION_TOKEN", "")).strip()
        source_id = str(st.secrets.get("NOTION_DATA_SOURCE_ID", "")).strip()
    except Exception:
        st.info("노션 연결 설정이 필요합니다.")
        return
    if not token or not source_id:
        st.info("노션 연결 설정이 필요합니다.")
        return
    try:
        with st.spinner("노션 기록을 읽고 있습니다…"):
            records = load_records(token, source_id)
    except BoardError as exc:
        st.error(str(exc))
        return
    except Exception:
        # Never render raw exceptions, requests, credentials, or Notion responses.
        st.error("기록을 읽는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")
        return
    checked_at = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    st.success(f"노션 연결됨 · 표시할 기록 {len(records)}건")
    st.caption(f"실제 조회: {checked_at} KST · 표시 순서 오름차순 · 같은 순서는 최근 수정순")
    if not records:
        st.info("아직 보드에 표시할 기록이 없습니다. 노션 기록부에 활동을 입력하고 ‘보드에 표시’를 체크해 주세요.")
        st.caption("‘표시 순서’에 1, 2, 3…을 입력하시면 원하는 순서로 표시됩니다. 입력 후 ‘기록 새로고침’을 눌러 주세요.")
    else:
        columns = st.columns(3)
        for index, record in enumerate(records):
            with columns[index % 3]:
                with st.container(border=True):
                    st.subheader(record["title"])
                    st.caption(" · ".join(filter(None, [record["category"], record["status"]])))
                    if record["note"]:
                        st.text(record["note"])
                    if record["next_goal"]:
                        st.caption("다음 목표")
                        st.text(record["next_goal"])



def render_market(st, quotes, reference):
    st.header("B · Bitcoin", divider="gray")
    st.write("가격과 금리를 살펴보며, 투자의 기준을 세웁니다.")
    for column, symbol, title in zip(st.columns(2), ("BTC", "USDT"), ("비트코인 · BTC", "테더 · USDT")):
        with column:
            with st.container(border=True):
                st.subheader(title)
                krw, usd = st.columns(2)
                with krw:
                    result = quotes["upbit"]
                    if result["data"] is not None:
                        st.metric("원화 · KRW", f"₩{result['data'][symbol]['price']:,.0f}")
                        quote_detail(st, result, symbol)
                    else:
                        st.metric("원화 · KRW", "—")
                        public_missing(st, "원화 시세")
                    st.caption(f"[업비트 원화 시장](https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{symbol}) · 실제 거래 가격")
                with usd:
                    result = quotes[symbol]
                    if result["data"] is not None:
                        decimals = 2 if symbol == "BTC" else 5
                        st.metric("달러 · USD", "$" + f"{result['data']['price']:,.{decimals}f}")
                        quote_detail(st, result)
                    else:
                        st.metric("달러 · USD", "—")
                        public_missing(st, "달러 시세")
                    st.caption(f"[Coinbase 달러 시장](https://api.exchange.coinbase.com/products/{symbol}-USD/ticker) · 실제 거래 가격")
    fx_col, kr_col, us_col = st.columns(3)
    with fx_col:
        with st.container(border=True):
            result = reference["fx"]
            data = result["data"]
            st.metric("원/달러 환율", "—" if data is None else f"{data['rate']:,.2f}원")
            st.caption("1 USD당 KRW · ECB 일일 참조환율")
            if data is not None:
                st.caption(f"자료 기준 {data['date']} · 유로 기준 두 환율로 계산")
                st.caption(f"조회 {kst_time(result['checked_at'])}")
                if (datetime.now(timezone.utc).date() - data["date"]).days > 7:
                    st.warning("환율 기준일이 7일 이상 지났습니다.")
            else:
                public_missing(st, "환율")
            st.caption("[유럽중앙은행 출처](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) · 영업일 갱신")
    with kr_col:
        with st.container(border=True):
            result = reference["bok"]
            data = result["data"]
            st.metric("한국 기준금리", "—" if data is None else f"{data['rate']:.2f}%")
            st.caption("한국은행 기준금리 · 연율")
            if data is not None:
                if data.get("provider") == "BIS":
                    st.caption(f"자료 기준 {data['date']} · BIS(한국은행 제공)")
                    st.caption("한국은행 직접 연결 지연으로 공식 대체 자료를 표시합니다.")
                    if data["stale"]:
                        st.warning("대체 자료의 기준일이 10일 이상 지났습니다. 최신 결정은 한국은행 출처를 확인해 주세요.")
                    st.caption(f"[BIS 자료 출처]({data['source']})")
                else:
                    st.caption(f"최근 변경 {data['date']}")
                st.caption(f"조회 {kst_time(result['checked_at'])}")
            else:
                public_missing(st, "한국 기준금리")
            st.caption(f"[한국은행 출처]({BOK_URL})")
    with us_col:
        with st.container(border=True):
            result = reference["us"]
            data = result["data"]
            st.metric("미국 기준금리", "—" if data is None else f"{data['low']:.2f}–{data['high']:.2f}%")
            st.caption("연방기금금리 목표범위 · 연율")
            if data is not None:
                st.caption(f"자료 기준 {data['date']}")
                st.caption(f"조회 {kst_time(result['checked_at'])}")
                if data["stale"]:
                    st.warning("미국 금리 자료의 갱신이 늦어지고 있습니다. 기준일을 확인해 주세요.")
            else:
                public_missing(st, "미국 기준금리")
            st.caption("[뉴욕 연방준비은행 출처](https://www.newyorkfed.org/markets/reference-rates/effr) · 공표된 목표범위")


def block_track(snapshot):
    cards = []
    for block in reversed((snapshot["recent_blocks"] or [])[:3]):
        at = block["mined_at"].astimezone(ZoneInfo("Asia/Seoul")).strftime("%m-%d %H:%M KST")
        cards.append(f'<a class="bts-block mined" href="{block["url"]}" target="_blank" rel="noopener noreferrer"><span>확정된 블록</span><strong>#{block["height"]:,}</strong><span>{block["tx_count"]:,}건</span><small>블록 시각 {at}</small></a>')
    if snapshot["recent_blocks"] and snapshot["pending_blocks"]:
        cards.append('<div class="bts-boundary">←<br>채굴</div>')
    for block in (snapshot["pending_blocks"] or [])[:3]:
        label = "이후 대기 묶음" if block["is_aggregate"] else ("다음 블록 예상" if block["position"] == 1 else f'{block["position"]}번째 예상')
        cards.append(f'<div class="bts-block pending"><span>{label}</span><strong>{block["tx_count"]:,}건</strong><span>중앙값 {block["median_fee_sat_vb"]:.2f} sat/vB</span><small>{block["vsize_vb"] / 1_000_000:.2f} MvB</small></div>')
    return '''<style>
    .bts-track{display:flex;gap:12px;overflow-x:auto;padding:8px 0 16px;align-items:center}
    .bts-block{box-sizing:border-box;flex:1 0 164px;min-height:155px;border-radius:14px;padding:18px 16px;display:flex;flex-direction:column;gap:8px;text-decoration:none!important;color:#172c44!important}
    .bts-block strong{font-size:23px}.bts-block span{font-size:13px}.bts-block small{font-size:11px}
    .bts-block.mined{background:#e5eef9;border:1px solid #bdd0e8}.bts-block.pending{background:#fff1da;border:1px dashed #d5a763}
    .bts-boundary{color:#718096;text-align:center;flex:0 0 28px;font-size:12px}
    </style><div class="bts-track">''' + "".join(cards) + "</div>"


def render_time(st, snapshot):
    st.header("T · Time", divider="gray")
    st.write("블록이 쌓이는 시간과, 줄어드는 신규 발행량을 봅니다.")
    st.caption(f"[mempool.space 출처](https://mempool.space/) · 조회 {kst_time(snapshot['fetched_at'])}")
    for message in snapshot["errors"].values():
        st.info(message)
    if snapshot["recent_blocks"] or snapshot["pending_blocks"]:
        st.markdown(block_track(snapshot), unsafe_allow_html=True)
        st.caption("파란색은 확정된 블록, 주황색은 대기 거래로 구성한 예상 블록입니다. 거래 유입과 수수료에 따라 구성은 달라집니다.")
    if snapshot["pending_blocks"] == []:
        st.info("현재 예상 대기 블록이 없습니다.")
    backlog, fees = snapshot["backlog"], snapshot["fees"]
    if backlog:
        first, second = st.columns(2)
        first.metric("확정 대기 거래", f"{backlog['tx_count']:,}건")
        second.metric("대기 거래 크기", f"{backlog['vsize_vb'] / 1_000_000:,.2f} MvB")
    if fees:
        for col, label, key in zip(st.columns(3), ("높은 우선순위", "약 30분 목표", "약 1시간 목표"), ("fastestFee", "halfHourFee", "hourFee")):
            col.metric(label, f"{fees[key]:g} sat/vB")
        st.caption("권장 수수료율 · sat/vB는 가상 바이트당 사토시입니다. 표시 시간 내 확정을 보장하지 않습니다.")
    data = snapshot["halving"]
    st.subheader("다음 반감기까지")
    if data is None:
        public_missing(st, "반감기 계산에 필요한 블록 높이")
        return
    cols = st.columns(3)
    cols[0].metric("현재 블록 높이", f"{data['height']:,}")
    cols[1].metric("남은 블록", f"{data['remaining_blocks']:,}개")
    cols[2].metric("다음 반감기 블록", f"{data['next_height']:,}")
    progress = data["cycle_progress"]
    st.progress(progress, text=f"이번 반감기 주기 {min(progress * 100, 99.99):.2f}% 진행")
    estimate = data["estimated_at"].astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    st.write(f"**예상 {estimate}경** · 신규 발행량 **{data['current_subsidy_btc']:g} → {data['next_subsidy_btc']:g} BTC/블록**")
    st.caption("조회 시점부터 블록당 평균 10분 가정 · 실제 일정은 달라질 수 있습니다. 신규 발행량에는 거래 수수료가 포함되지 않습니다.")
    st.caption("[비트코인 반감기 현황](https://mempool.space/graphs/mining/block-rewards) · 210,000블록마다 신규 발행량 감소")
    last_block = snapshot["recent_blocks"][0]
    if (snapshot["fetched_at"] - last_block["mined_at"]).total_seconds() > 7200:
        st.warning("최근 블록 시각이 2시간 이상 지났습니다. 블록 높이와 예상일을 출처에서 확인해 주세요.")


def main():
    import streamlit as st
    st.set_page_config(page_title="BTS 투자철학", page_icon="🌱", layout="wide")
    st.title("BTS 투자철학")
    st.caption("Bitcoin · Time · Self")
    st.markdown("[B · 가격과 금리](#b-bitcoin)　 [T · 블록과 반감기](#t-time)　 [S · 나의 기록](#s-self)")

    @st.cache_data(ttl=55, show_spinner=False, max_entries=1)
    def market_quotes():
        return collect_public({"upbit": load_upbit, "BTC": lambda: load_usd("BTC-USD"), "USDT": lambda: load_usd("USDT-USD")})

    @st.cache_data(ttl=3600, show_spinner=False, max_entries=1)
    def reference_values():
        return collect_public({"fx": load_fx, "bok": load_bok, "us": load_us_policy})

    @st.cache_data(ttl=55, show_spinner=False, max_entries=1)
    def time_values():
        return fetch_time_snapshot(public_json)

    @st.fragment(run_every=60)
    def public_sections():
        if st.button("자료 새로고침", help="가격·환율·금리·블록 자료를 제공처에서 다시 읽습니다."):
            market_quotes.clear()
            reference_values.clear()
            time_values.clear()
        st.caption("화면이 열려 있는 동안 시세·블록은 1분마다 갱신합니다. 환율·기준금리는 1시간마다 확인합니다.")
        with st.spinner("가격과 기준 자료를 읽고 있습니다…"):
            quotes = market_quotes()
            reference = reference_values()
        render_market(st, quotes, reference)
        with st.spinner("블록 진행 상황을 읽고 있습니다…"):
            snapshot = time_values()
        render_time(st, snapshot)

    public_sections()
    render_self(st)


if __name__ == "__main__":
    main()
