"""
L-Auto Onboarding - 서류 파싱 모듈 (Claude Vision)
사업자등록증 / 통장사본 이미지(또는 PDF)를 Claude API로 읽어 항목을 추출한다.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

CLAUDE_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class ParseError(Exception):
    """사용자에게 보여줄 수 있는 파싱 오류"""


# ---------------------------------------------------------------
# 1. 파일 형식 확인 (확장자가 아니라 파일 내용으로 판별)
# ---------------------------------------------------------------
def detect_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"%PDF":
        return "application/pdf"
    return ""


# ---------------------------------------------------------------
# 2. 지시문 + 응답 형식 (도구 형식으로 받아서 항상 정해진 칸으로 응답)
# ---------------------------------------------------------------
BIZ_PROMPT = """이 이미지는 한국의 사업자등록증이다. 항목을 이미지에 적힌 글자 그대로 추출해서 save_business_info 도구로 저장해라.
- 보이지 않거나 없는 항목은 빈 문자열("")로 둔다. 절대 추측해서 채우지 않는다.
- 개인사업자는 법인등록번호가 없으므로 빈 문자열.
- 개업연월일은 YYYY-MM-DD 형식.
- 업태/종목이 여러 개면 쉼표로 구분.
- 대표자 생년월일이 적혀 있으면(주로 개인사업자) YYYY-MM-DD로, 없으면 빈 문자열. 주민등록번호 뒷자리는 절대 적지 않는다.
- 사업장 소재지는 address에 전체를 적고, address_road(시/도~건물번호까지)와 address_detail(층·호, 괄호 안 동명/건물명 등 나머지)로도 나눠 적는다.
- 사업자등록증이 아닌 이미지면 is_valid_doc을 false로."""

BANK_PROMPT = """이 이미지는 한국의 통장사본(통장 표지 또는 계좌 개설 확인서)이다. 항목을 이미지에 적힌 글자 그대로 추출해서 save_bank_info 도구로 저장해라.
- 보이지 않거나 없는 항목은 빈 문자열("")로 둔다. 절대 추측해서 채우지 않는다.
- 계좌번호는 숫자와 하이픈(-)만 그대로 적는다.
- 통장사본이 아닌 이미지면 is_valid_doc을 false로."""

BIZ_FIELDS = {
    "trade_name": "상호 또는 법인명",
    "biz_no": "사업자등록번호",
    "ceo_name": "대표자 성명",
    "open_date": "개업연월일 (YYYY-MM-DD)",
    "corp_no": "법인등록번호 (개인사업자는 빈 문자열)",
    "biz_type": "업태",
    "biz_item": "종목",
    "address": "사업장 소재지 전체",
    "address_road": "도로명주소 (시/도 ~ 건물번호)",
    "address_detail": "상세주소 (층·호, 괄호 안 동명/건물명)",
    "birth_date": "대표자 생년월일 YYYY-MM-DD (없으면 빈 문자열)",
}
BANK_FIELDS = {
    "bank_name": "은행명",
    "account_no": "계좌번호",
    "account_holder": "예금주",
}


def make_tool(name: str, desc: str, fields: dict) -> dict:
    props = {"is_valid_doc": {"type": "boolean", "description": "해당 서류가 맞으면 true"}}
    props.update({k: {"type": "string", "description": v} for k, v in fields.items()})
    return {
        "name": name,
        "description": desc,
        "input_schema": {"type": "object", "properties": props, "required": list(props)},
    }


BIZ_TOOL = make_tool("save_business_info", "사업자등록증에서 추출한 항목 저장", BIZ_FIELDS)
BANK_TOOL = make_tool("save_bank_info", "통장사본에서 추출한 항목 저장", BANK_FIELDS)


# ---------------------------------------------------------------
# 3. Claude API 호출
# ---------------------------------------------------------------
def call_claude(api_key: str, model: str, prompt: str, tool: dict, data_b64: str, mime: str) -> dict:
    if mime == "application/pdf":
        file_block = {"type": "document", "source": {"type": "base64", "media_type": mime, "data": data_b64}}
    else:
        file_block = {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data_b64}}

    # 일부 모델은 tool_choice 강제(type: tool)를 지원하지 않으므로 auto로 두고 지시문으로 도구 사용을 요청
    body = {
        "model": model,
        "max_tokens": 4096,
        "tools": [tool],
        "tool_choice": {"type": "auto"},
        "messages": [{"role": "user", "content": [
            file_block,
            {"type": "text", "text": prompt + f"\n- 반드시 {tool['name']} 도구를 호출해서 결과를 저장해라. 다른 설명은 쓰지 마라."},
        ]}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    try:
        res = requests.post(CLAUDE_URL, headers=headers, json=body, timeout=90)
    except requests.exceptions.Timeout:
        raise ParseError("AI 응답 시간이 초과되었습니다. 다시 시도해 주세요.")
    except requests.exceptions.ConnectionError:
        raise ParseError("Claude 서버에 연결할 수 없습니다. 인터넷/사내망 차단 여부를 확인하세요.")

    try:
        data = res.json()
    except ValueError:
        data = {}

    if res.status_code != 200:
        err = data.get("error") or {}
        etype, msg = err.get("type", ""), err.get("message", f"HTTP {res.status_code}")
        if res.status_code == 401 or etype == "authentication_error":
            raise ParseError("Claude API 키가 올바르지 않습니다. 키 설정을 확인하세요.")
        if res.status_code == 404 or "model" in msg.lower() and etype == "not_found_error":
            raise ParseError(f"모델 이름({model})을 찾을 수 없습니다. CLAUDE_MODEL 설정을 확인하세요.")
        if res.status_code == 429:
            raise ParseError("호출 한도를 넘었습니다. 잠시 후 다시 시도하세요.")
        if res.status_code == 400 and "credit" in msg.lower():
            raise ParseError("API 크레딧이 부족합니다. Claude Console에서 결제 정보를 확인하세요.")
        if res.status_code in (500, 529):
            raise ParseError("AI 서버가 혼잡합니다. 잠시 후 다시 시도하세요.")
        raise ParseError(f"Claude 오류: {msg}")

    content = data.get("content", [])
    # 1순위: 도구 호출 결과
    for block in content:
        if block.get("type") == "tool_use" and block.get("name") == tool["name"]:
            return block.get("input") or {}
    # 2순위: 글로 답한 경우 그 안의 JSON을 찾아서 사용
    text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            pass
    raise ParseError("AI 응답에서 추출 결과를 찾을 수 없습니다. 다시 시도해 주세요.")


# ---------------------------------------------------------------
# 4. 값 정리 (형식 통일)
# ---------------------------------------------------------------
def _digits(v: str) -> str:
    return re.sub(r"\D", "", v or "")


def fmt_biz_no(v: str) -> str:
    d = _digits(v)
    return f"{d[:3]}-{d[3:5]}-{d[5:]}" if len(d) == 10 else (v or "").strip()


def fmt_corp_no(v: str) -> str:
    d = _digits(v)
    return f"{d[:6]}-{d[6:]}" if len(d) == 13 else (v or "").strip()


def fmt_birth(v: str) -> str:
    """생년월일을 YYMMDD 6자리로 (주민번호 뒷자리가 섞여 와도 앞 6자리만 사용)"""
    d = _digits(v)
    if len(d) == 8:          # 19971221
        return d[2:]
    if len(d) >= 6:          # 971221 또는 9712211xxxxxx
        return d[:6]
    return ""


def fmt_account(v: str) -> str:
    return re.sub(r"[^\d-]", "", v or "")


def clean(result: dict, fields) -> dict:
    return {k: str(result.get(k) or "").strip() for k in fields}


# ---------------------------------------------------------------
# 5. 전체 실행: 서류 2종을 동시에 파싱
# ---------------------------------------------------------------
def parse_documents(api_key: str, model: str, biz: tuple, bank: tuple) -> dict:
    """biz, bank = (base64 문자열, mime)"""
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_biz = ex.submit(call_claude, api_key, model, BIZ_PROMPT, BIZ_TOOL, biz[0], biz[1])
        f_bank = ex.submit(call_claude, api_key, model, BANK_PROMPT, BANK_TOOL, bank[0], bank[1])
        raw_biz, raw_bank = f_biz.result(), f_bank.result()

    b = clean(raw_biz, BIZ_FIELDS)
    b["biz_no"] = fmt_biz_no(b["biz_no"])
    b["corp_no"] = fmt_corp_no(b["corp_no"])
    b["birth_date"] = fmt_birth(b["birth_date"])
    if not b["address_road"] and not b["address_detail"]:
        b["address_road"] = b["address"]

    k = clean(raw_bank, BANK_FIELDS)
    k["account_no"] = fmt_account(k["account_no"])

    warnings = []
    if raw_biz.get("is_valid_doc") is False:
        warnings.append("사업자등록증으로 보이지 않는 파일이 올라왔습니다.")
    if raw_bank.get("is_valid_doc") is False:
        warnings.append("통장사본으로 보이지 않는 파일이 올라왔습니다.")
    if b["biz_no"] and len(_digits(b["biz_no"])) != 10:
        warnings.append("사업자등록번호가 10자리가 아닙니다. 원본을 확인하세요.")

    return {
        "biz": b,
        "bank": k,
        "warnings": warnings,
        "elapsed": round(time.time() - t0, 1),
        "model": model,
    }
