"""BTS 투자철학: read-only Notion Self cards. Credentials live in st.secrets."""
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


def main():
    import streamlit as st
    st.set_page_config(page_title="BTS 투자철학", page_icon="🌱", layout="wide")
    st.title("BTS 투자철학")
    st.caption("Bitcoin · Time · Self")
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
    st.divider()
    st.caption("B · 비트코인·테더·환율·기준금리 / T · 멤풀·반감기 — 다음 단계에서 연결합니다.")


if __name__ == "__main__":
    main()
