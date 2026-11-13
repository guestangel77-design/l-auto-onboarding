"""
L-Auto Onboarding - 상신 전 사전 검증 모듈
  0. 사업자번호 형식 검사 (검증번호 계산)      → 실제 작동
  1. 국세청 휴업·폐업 확인 + 과세유형 + 진위확인 → 실제 작동 (공공데이터포털 API)
                                                 ※ 샘플 가상 번호만 시연용 결과(Mock)
  2. 예금주 ↔ 상호/대표자 일치 확인            → 실제 작동
  3. 압류 DB 대조                              → Mock (샘플 CSV, 준법지원팀 데이터로 교체 예정)

각 검사 결과 status: pass(통과) / warn(주의, 상신 가능) / fail(불가) / error(확인 불가) / skip(조회 생략)
"""
import csv
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests

NTS_BASE = "https://api.odcloud.kr/api/nts-businessman/v1"


def digits(v: str) -> str:
    return re.sub(r"\D", "", v or "")


def result(key, title, status, message, mode="real", detail=None):
    return {"key": key, "title": title, "status": status, "message": message,
            "mode": mode, "detail": detail or {}}


# ---------------------------------------------------------------
# 0. 사업자등록번호 형식 검사 (국세청 검증번호 규칙)
# ---------------------------------------------------------------
def check_biz_format(biz_no: str) -> dict:
    d = digits(biz_no)
    title = "사업자번호 형식 검사"
    if len(d) != 10:
        return result("format", title, "fail", f"사업자번호가 10자리가 아닙니다. (현재 {len(d)}자리)")
    w = [1, 3, 7, 1, 3, 7, 1, 3, 5]
    s = sum(int(d[i]) * w[i] for i in range(9)) + (int(d[8]) * 5) // 10
    if (10 - s % 10) % 10 != int(d[9]):
        return result("format", title, "fail", "검증번호가 맞지 않습니다. 숫자 1자리가 잘못 읽혔을 수 있습니다.")
    return result("format", title, "pass", "형식이 올바릅니다.")


# ---------------------------------------------------------------
# 1. 국세청 API
# ---------------------------------------------------------------
def _nts_post(api_key: str, path: str, body: dict) -> dict:
    try:
        res = requests.post(
            f"{NTS_BASE}/{path}",
            params={"serviceKey": api_key, "returnType": "JSON"},
            json=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=15,
        )
    except requests.exceptions.Timeout:
        raise RuntimeError("국세청 응답 시간이 초과되었습니다.")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("국세청 서버에 연결할 수 없습니다. 인터넷/사내망 차단 여부를 확인하세요.")
    try:
        data = res.json()
    except ValueError:
        data = {}
    if res.status_code != 200:
        msg = data.get("msg") or data.get("message") or f"HTTP {res.status_code}"
        if res.status_code in (401, 403) or "인증키" in str(msg) or "SERVICE_KEY" in str(msg).upper():
            raise RuntimeError("국세청 인증키가 올바르지 않거나 아직 활성화되지 않았습니다. (승인 직후면 1~2시간 뒤 재시도)")
        raise RuntimeError(f"국세청 API 오류: {msg}")
    return data


def check_nts_status(api_key: str, biz_no: str) -> list:
    """국세청 상태조회 1번으로 [휴업·폐업 확인, 과세유형 확인] 두 줄을 만든다"""
    t_state, t_tax = "국세청 휴업·폐업 확인", "국세청 과세유형 확인"
    d = digits(biz_no)

    def fail_both(status, msg):
        return [result("nts_state", t_state, status, msg),
                result("nts_tax", t_tax, "skip", "사업자 상태를 확인하지 못해 조회하지 않았습니다.")]

    if not api_key:
        return fail_both("error", "국세청 API 키(NTS_API_KEY)가 설정되지 않았습니다.")
    if len(d) != 10:
        return fail_both("error", "사업자번호가 올바르지 않아 조회하지 못했습니다.")
    try:
        data = _nts_post(api_key, "status", {"b_no": [d]})
    except RuntimeError as e:
        return fail_both("error", str(e))

    row = (data.get("data") or [{}])[0]
    stt, stt_cd = row.get("b_stt", ""), row.get("b_stt_cd", "")
    tax, tax_cd = (row.get("tax_type") or "").strip(), row.get("tax_type_cd", "")
    end_dt = row.get("end_dt") or ""
    end_txt = f"{end_dt[:4]}-{end_dt[4:6]}-{end_dt[6:]}" if len(end_dt) == 8 else "-"

    # ① 휴업·폐업
    if not stt_cd:
        return fail_both("fail", tax or "국세청에 등록되지 않은 사업자등록번호입니다.")
    if stt_cd == "01":
        state = result("nts_state", t_state, "pass", "계속사업자 (정상 영업 중)")
    elif stt_cd == "02":
        state = result("nts_state", t_state, "fail", "휴업 중인 사업자입니다. 거래 등록 불가", detail={"사업자 상태": stt})
    elif stt_cd == "03":
        state = result("nts_state", t_state, "fail", f"폐업한 사업자입니다. 거래 등록 불가 (폐업일 {end_txt})",
                       detail={"사업자 상태": stt, "폐업일": end_txt})
    else:
        state = result("nts_state", t_state, "warn", f"사업자 상태를 확인하세요: {stt or '-'}")

    # ② 과세유형
    tax_name = tax.replace("부가가치세 ", "").replace(" 입니다.", "").replace("입니다.", "").strip() or "-"
    if tax_cd in ("01", "07"):
        taxr = result("nts_tax", t_tax, "pass", tax_name)
    elif tax_cd == "02":
        taxr = result("nts_tax", t_tax, "warn", f"{tax_name} — 세금계산서 발급이 안 될 수 있습니다. 정산 방식 확인 필요")
    elif tax_cd == "04":
        taxr = result("nts_tax", t_tax, "warn", f"{tax_name} — 세금계산서 대신 계산서 발급 대상입니다.")
    else:
        taxr = result("nts_tax", t_tax, "warn", f"{tax_name} — 과세유형을 확인하세요.")
    return [state, taxr]


def check_nts_validate(api_key: str, biz: dict) -> dict:
    title = "국세청 진위확인 (번호·대표자·개업일)"
    d = digits(biz.get("biz_no"))
    start_dt = digits(biz.get("open_date"))
    ceo = (biz.get("ceo_name") or "").strip()
    if not api_key:
        return result("nts_validate", title, "error", "국세청 API 키(NTS_API_KEY)가 설정되지 않았습니다.")
    if len(d) != 10 or len(start_dt) != 8 or not ceo:
        return result("nts_validate", title, "error", "사업자번호·개업일자·대표자명 중 비어 있거나 형식이 틀린 항목이 있습니다.")
    body = {"businesses": [{
        "b_no": d, "start_dt": start_dt, "p_nm": ceo,
        "p_nm2": "", "b_nm": "", "corp_no": "", "b_sector": "", "b_type": "", "b_adr": "",
    }]}
    try:
        data = _nts_post(api_key, "validate", body)
    except RuntimeError as e:
        return result("nts_validate", title, "error", str(e))
    row = (data.get("data") or [{}])[0]
    if row.get("valid") == "01":
        return result("nts_validate", title, "pass", "국세청 등록 정보와 일치합니다.")
    return result("nts_validate", title, "fail",
                  "국세청 등록 정보와 일치하지 않습니다. 대표자명·개업일자를 원본과 대조하세요.",
                  detail={"국세청 응답": row.get("valid_msg") or "-"})


# ---------------------------------------------------------------
# 2. 예금주 일치 확인
# ---------------------------------------------------------------
_CORP_WORDS = r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(사\)|사단법인|\(재\)|재단법인|\(합\)|합자회사|\(합명\)|합명회사"


def norm_name(s: str) -> str:
    s = re.sub(_CORP_WORDS, "", s or "")
    return re.sub(r"[\s()\[\]·.,\-_/]", "", s).lower()


def check_holder(biz: dict, bank: dict) -> dict:
    title = "예금주 ↔ 상호·대표자 일치"
    h, t, c = norm_name(bank.get("account_holder")), norm_name(biz.get("trade_name")), norm_name(biz.get("ceo_name"))
    is_corp = len(digits(biz.get("corp_no"))) == 13
    detail = {"예금주": bank.get("account_holder") or "-", "상호": biz.get("trade_name") or "-", "대표자": biz.get("ceo_name") or "-"}

    if not h:
        return result("holder", title, "fail", "예금주가 비어 있습니다.", detail=detail)
    if t and h == t:
        return result("holder", title, "pass", "예금주가 상호와 일치합니다.", detail=detail)
    if c and h == c:
        if is_corp:
            return result("holder", title, "fail", "법인사업자인데 대표자 개인 명의 계좌입니다. 법인 명의 계좌가 필요합니다.", detail=detail)
        return result("holder", title, "pass", "예금주가 대표자와 일치합니다. (개인사업자)", detail=detail)
    if not is_corp and t and c and h in (c + t, t + c):
        return result("holder", title, "pass", "예금주가 '대표자(상호)' 형식으로 일치합니다. (개인사업자)", detail=detail)
    if (t and t in h) or (c and c in h and not is_corp):
        return result("holder", title, "warn", "예금주에 상호/대표자가 포함되어 있습니다. 표기 차이인지 확인하세요.", detail=detail)
    return result("holder", title, "fail", "예금주가 상호·대표자와 일치하지 않습니다. (제3자 명의 계좌 의심)", detail=detail)


# ---------------------------------------------------------------
# 3. 압류 DB 대조 (지금은 Mock: 샘플 파일 / 준법지원팀 파일로 교체하면 실제 작동)
#    - CSV(.csv) 또는 엑셀(.xlsx) 모두 가능
#    - 첫 줄(제목줄)에 '사업자'가 들어간 열을 자동으로 찾아 사업자번호로 대조
#    - 파일은 내 PC에서만 읽고 외부로 보내지 않음
# ---------------------------------------------------------------
def load_seizure_rows(db_path: str) -> list:
    if db_path.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        ws = load_workbook(db_path, read_only=True, data_only=True).active
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
    else:
        with open(db_path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    return [dict(zip(header, r)) for r in rows[1:]]


def check_seizure(db_path: str, biz_no: str, mode: str = "mock") -> dict:
    title = "압류 DB 대조"
    d = digits(biz_no)
    if not os.path.exists(db_path):
        return result("seizure", title, "error", f"압류 DB 파일을 찾을 수 없습니다: {os.path.basename(db_path)}", mode=mode)
    try:
        rows = load_seizure_rows(db_path)
    except Exception:
        return result("seizure", title, "error", "압류 DB 파일을 읽을 수 없습니다. 파일 형식을 확인하세요.", mode=mode)
    if not rows:
        return result("seizure", title, "error", "압류 DB 파일이 비어 있습니다.", mode=mode)
    key = next((k for k in rows[0] if "사업자" in k), None)
    if not key:
        return result("seizure", title, "error", "압류 DB 첫 줄에 '사업자등록번호' 열이 없습니다.", mode=mode)
    hits = [r for r in rows if digits(r.get(key)) == d]
    if hits:
        r = hits[0]
        kind = next((v for k, v in r.items() if "구분" in k or "종류" in k), "")
        date = next((v for k, v in r.items() if "일" in k and "번호" not in k), "")
        return result("seizure", title, "fail",
                      f"압류 등록 업체입니다. ({kind or '압류'}{', 접수 ' + date if date else ''})",
                      mode=mode, detail={"압류 건수": str(len(hits))})
    return result("seizure", title, "pass", f"압류 등록 내역이 없습니다. (DB {len(rows)}건 대조)", mode=mode)


# ---------------------------------------------------------------
# 전체 실행
# ---------------------------------------------------------------
def demo_nts_checks(info: dict, biz: dict) -> tuple:
    """샘플(가상) 사업자번호용 국세청 결과 — 🟡 시연용 Mock"""
    note = " (시연용 가상 번호)"
    status = [
        result("nts_state", "국세청 휴업·폐업 확인", "pass", f"{info['state']} (정상 영업 중){note}", mode="mock"),
        result("nts_tax", "국세청 과세유형 확인", "pass", info["tax"] + note, mode="mock"),
    ]
    title = "국세청 진위확인 (번호·대표자·개업일)"
    same = (biz.get("ceo_name") or "").strip() == info["ceo"] and digits(biz.get("open_date")) == info["open_date"]
    valid = (result("nts_validate", title, "pass", "국세청 등록 정보와 일치합니다." + note, mode="mock") if same else
             result("nts_validate", title, "fail", "국세청 등록 정보와 일치하지 않습니다. 대표자명·개업일자를 원본과 대조하세요." + note, mode="mock"))
    return status, valid


def run_all(nts_key: str, seizure_db: str, biz: dict, bank: dict, seizure_mode: str = "mock",
            demo_nts: dict = None) -> dict:
    fmt = check_biz_format(biz.get("biz_no"))
    demo = (demo_nts or {}).get(digits(biz.get("biz_no")))
    if demo:
        status, valid = demo_nts_checks(demo, biz)
    else:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_status = ex.submit(check_nts_status, nts_key, biz.get("biz_no"))
            f_valid = ex.submit(check_nts_validate, nts_key, biz)
            status, valid = f_status.result(), f_valid.result()
    checks = [fmt, *status, valid, check_holder(biz, bank), check_seizure(seizure_db, biz.get("biz_no"), seizure_mode)]

    blockers = [c for c in checks if c["status"] in ("fail", "error")]
    warns = [c for c in checks if c["status"] == "warn"]
    if blockers:
        verdict = {"ok": False, "message": f"상신 불가 — {len(blockers)}개 항목을 해결해야 합니다."}
    elif warns:
        verdict = {"ok": True, "message": f"상신 가능 — 주의 항목 {len(warns)}개를 확인하세요."}
    else:
        verdict = {"ok": True, "message": "상신 가능 — 모든 사전 검증을 통과했습니다."}
    return {"checks": checks, "verdict": verdict}
